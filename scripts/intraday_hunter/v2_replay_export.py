"""IH v2 prompt replay, step 1 of 3 — READ-ONLY export of Call 2 inputs + scoring data.

Runs INSIDE the backend container (it uses the deployed app code and DB):

    docker cp scripts/intraday_hunter/v2_replay_export.py st-backend:/tmp/replay/
    docker exec -w /app -e PYTHONPATH=/app st-backend \
        python /tmp/replay/v2_replay_export.py --days 2026-10-09,2026-10-12 > export.json

For each day it rebuilds the exact live Call 2 inputs minute by minute (09:16 → 09:30) with
`decision.build_live` — candles, the teacher plan, OI flow, VIX — and injects the order flow live
Call 2 saw from that day's `ih_minute_log` (minute − 1). `--cand-levels path/to/levels.py` adds a
second input set built with a candidate `levels.py` (variant "cand"); the deployed code gives
variant "base". It also exports the captured option contracts (rebuilt from the day's option
symbols when the 3-day Redis capture has expired), every option's 1m closes, the index closes,
the day's Call 1 thesis, live v2's decisions and the stored grade arms.

Safety: the session is SET TRANSACTION READ ONLY and rolled back; every DB read is cached so
each query runs once (a few hundred light queries).
"""
import argparse
import asyncio
import importlib.util
import json
import re
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select, text

from app.core.constants import IST
from app.core.database import async_session_factory
from app.models.ih_v2 import IhDayGrade, IhMinuteLog
from app.services.intraday_hunter import data as data_mod
from app.services.intraday_hunter import store
from app.services.intraday_hunter_v2 import capture, decision, oi_flow
from app.services.intraday_hunter_v2 import context as ctx_mod
from app.services.intraday_hunter_v2 import levels as L
from app.services.intraday_hunter_v2.params import v2_params_async

SYM = re.compile(r"^(NSE|BSE):(NIFTY|BANKNIFTY|SENSEX)(\d{2})([A-Z]{3}|[1-9OND]\d{2})(\d+)(CE|PE)$")
_cache: dict = {}


def _cached(name, fn):
    async def wrapper(session, *a, **k):
        key = repr((name, a, sorted(k.items())))
        if key not in _cache:
            _cache[key] = await fn(session, *a, **k)
        return _cache[key]
    return wrapper


fetch_day = _cached("day", data_mod.fetch_day)
ctx_mod.fetch_day = fetch_day
ctx_mod.prev_trading_date = _cached("prev", data_mod.prev_trading_date)
ctx_mod.fetch_vix = _cached("vix", ctx_mod.fetch_vix)
oi_flow.fetch_oi_sums = _cached("oi", oi_flow.fetch_oi_sums)


def _hm(c) -> str:
    ts = c["ts"]
    return ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)[11:16]


def _load(path: str):
    spec = importlib.util.spec_from_file_location("cand_levels", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _order_flow_by_minute(s, d: date) -> dict[str, dict]:
    rows = (await s.execute(select(IhMinuteLog).where(IhMinuteLog.trading_date == d)
                            .order_by(IhMinuteLog.minute_ts))).scalars().all()
    out: dict[str, dict] = {}
    for r in rows:
        of = (r.features or {}).get("order_flow") or {}
        slot = out.setdefault(r.minute_ts.astimezone(IST).strftime("%H:%M"), {})
        if of.get("fut"):
            slot[f"{r.index}_FUT"] = of["fut"]
        syms = of.get("atm_symbols") or {}
        for k, leg in (("CE", "atm_ce"), ("PE", "atm_pe")):
            if of.get(leg) and syms.get(k):
                slot[syms[k]] = of[leg]
    return out


async def main(days: list[date], cand_levels: str | None, cand_deadline: str) -> dict:
    orig = (L.level_facts, L.describe_facts)
    cand = _load(cand_levels) if cand_levels else None
    out: dict = {}
    async with async_session_factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        params = await v2_params_async()
        out["_params"] = params
        out["_cand_deadline"] = cand_deadline
        for d in days:
            day: dict = {}
            run = await store.get_run(s, d, "v2")
            day["call1"] = run.call1_json if run else None
            day["live_v2"] = ({"decision": run.decision, "direction": run.direction,
                               "history": run.call2_history} if run else None)
            ofl = await _order_flow_by_minute(s, d)
            variants = [("base", orig, params["call2_deadline"])]
            if cand:
                variants.append(("cand", (cand.level_facts, cand.describe_facts), cand_deadline))
            for name, (lf, df), deadline in variants:
                L.level_facts, L.describe_facts = lf, df
                p = {**params, "call2_deadline": deadline}
                mins = {}
                for m in range(16, 31):
                    now = time(9, m)
                    live = await decision.build_live(s, d, now, p)
                    if live is None:
                        continue
                    live.pop("_facts", None)
                    prev_min = (datetime.combine(d, now) - timedelta(minutes=1)).strftime("%H:%M")
                    live["order_flow_last_minute"] = ofl.get(prev_min) or "not available"
                    mins[now.strftime("%H:%M")] = live
                day[name] = mins
            L.level_facts, L.describe_facts = orig
            cap = await capture.load_capture(d)
            lo = datetime(d.year, d.month, d.day, 3, 30, tzinfo=timezone.utc)
            syms = (await s.execute(text(
                "SELECT DISTINCT symbol FROM market_data_1m WHERE timestamp >= :a AND timestamp < :b "
                "AND (symbol LIKE '%CE' OR symbol LIKE '%PE')"),
                {"a": lo, "b": lo + timedelta(hours=6, minutes=30)})).scalars().all()
            contracts = cap.get("contracts") or {}
            if not contracts:
                for sym in syms:
                    mm = SYM.match(sym)
                    if mm:
                        contracts.setdefault(mm.group(2), []).append(
                            {"strike": float(mm.group(5)), "type": mm.group(6), "symbol": sym})
            day["contracts"] = contracts
            day["contracts_source"] = "capture" if cap.get("contracts") else "rebuilt"
            day["premium_closes"] = {}
            for sym in syms:
                cs = await fetch_day(s, sym, d)
                if cs:
                    day["premium_closes"][sym] = {_hm(c): float(c["close"]) for c in cs}
            day["index_closes"] = {i: {_hm(c): float(c["close"]) for c in await fetch_day(s, i, d)}
                                   for i in L.INDICES}
            g = (await s.execute(select(IhDayGrade).where(IhDayGrade.trading_date == d))).scalar_one_or_none()
            day["grade_arms"] = g.arms if g else None
            out[str(d)] = day
        await s.rollback()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", required=True, help="comma-separated YYYY-MM-DD")
    ap.add_argument("--cand-levels", help="candidate levels.py → variant 'cand'")
    ap.add_argument("--cand-deadline", default="09:30", help="Call 2 deadline for variant 'cand'")
    a = ap.parse_args()
    ds = [date.fromisoformat(x) for x in a.days.split(",")]
    print(json.dumps(asyncio.run(main(ds, a.cand_levels, a.cand_deadline)), default=str))
