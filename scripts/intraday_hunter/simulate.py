#!/usr/bin/env python3
"""Backtest the Intraday Hunter agent day-by-day and grade it against what actually happened.

For each trading day in a range, runs the real Call 1 (thesis) + Call 2 watcher loop
(09:18 -> recheck on WAIT, cap 09:30, prior decisions fed back), then computes the day's
ACTUAL index behaviour and scores the agent's final decision against it. Engine-independent
(reads index price paths, not option fills) — the same honesty as the S2/S5 studies.

Per-day LLM results are cached to /tmp/ih_sim/<date>_<model>.json so a re-run (after a rate
limit / interrupt) is incremental. Use --refresh to recompute.

Usage:
    python scripts/intraday_hunter/simulate.py --start 2026-06-01 --end 2026-06-19
    python scripts/intraday_hunter/simulate.py --start 2026-06-01 --end 2026-06-19 --model claude-opus-4-8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, time

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from app.services.intraday_hunter import charts, context, llm_cli, prompts  # noqa: E402
from prototype_agent import (  # noqa: E402
    _calendar, _fetch_day, _fetch_vix, _prev_trading_date, _resolve_token, _slim_decision,
    CALL2_START, CALL2_DEADLINE, CHART_DIR,
)

DEFAULT_DB = "postgresql://trader:trader_dev_123@localhost:5433/stocktrading"
SIM_DIR = "/tmp/ih_sim"
LEAD = "BANKNIFTY"  # headline index for scoring (his primary)


# ── run the agent for one day, return structured result (cached) ─────────────
async def decide_day(conn, d: date, model: str, token: str | None, refresh: bool,
                     variant: str = "A", sim_dir: str = SIM_DIR,
                     start_time: time = CALL2_START, collapse_wait: bool = False) -> dict:
    os.makedirs(sim_dir, exist_ok=True)
    cache = os.path.join(sim_dir, f"{d.isoformat()}_{model}.json")
    if os.path.exists(cache) and not refresh:
        return json.load(open(cache))

    ds = d.isoformat()
    per_prev: dict[str, dict] = {}
    prevday_imgs: list[str] = []
    for idx in context.INDICES:
        pd = await _prev_trading_date(conn, idx, d)
        if pd is None:
            continue
        prev_candles = await _fetch_day(conn, idx, pd)
        if not prev_candles:
            continue
        s = context.prev_day_structure(prev_candles, idx)
        s["prev_date"] = pd.isoformat()
        per_prev[idx] = s
        lv = {k: s[k] for k in ("prev_close", "pdh", "pdl", "round")}
        prevday_imgs.append(charts.render_prev_day_chart(idx, prev_candles, lv, CHART_DIR, ds))
    if not per_prev:
        return {"date": ds, "error": "no prev-day data"}

    prev_date = next(iter(per_prev.values()))["prev_date"]
    vix1 = await _fetch_vix(conn, datetime.strptime(prev_date, "%Y-%m-%d").date())
    cal = _calendar(d)
    sysp = prompts.build_system_prompt(variant)
    c1 = await llm_cli.call_claude_json(
        sysp + "\n\n" + prompts.build_call1_prompt(
            context.build_call1_context(per_prev, [], cal, india_vix=vix1)),
        image_paths=prevday_imgs,
        required_keys=("trapped_side", "thesis", "conditional_plan"), token=token, model=model,
    )
    result = {"date": ds, "model": model, "vix": vix1, "call1": c1, "decisions": [], "final": None}
    if not c1:
        result["final"] = {"decision": "SKIP", "note": "call1 failed -> SKIP"}
        json.dump(result, open(cache, "w"), indent=2)
        return result

    today = {idx: await _fetch_day(conn, idx, d) for idx in per_prev}
    prior: list[dict] = []
    cp = start_time
    final = None
    while cp <= CALL2_DEADLINE:
        hhmm = cp.strftime("%H:%M")
        per_open, per_opening, opening_imgs = {}, {}, []
        for idx in per_prev:
            opening = [c for c in today[idx] if c["ts"][11:16] <= hhmm]
            if not opening:
                continue
            per_open[idx] = float(opening[0]["open"])
            per_opening[idx] = opening
            lv = {k: per_prev[idx][k] for k in ("prev_close", "pdh", "pdl", "round")}
            opening_imgs.append(charts.render_opening_chart(idx, opening, lv, CHART_DIR, ds))
        if not per_open:
            break
        mins = (cp.hour * 60 + cp.minute) - (9 * 60 + 15)
        vix2 = await _fetch_vix(conn, d, upto=cp)
        live = context.build_call2_live(per_open, per_prev, per_opening, hhmm, mins, india_vix=vix2)
        out = await llm_cli.call_claude_json(
            sysp + "\n\n" + prompts.build_call2_prompt(c1, live, prior),
            image_paths=prevday_imgs + opening_imgs,
            required_keys=("decision", "confidence"), token=token, model=model,
        )
        if not out:
            out = {"decision": "SKIP", "note": "call2 failed -> SKIP", "confidence": 0}
        out["_at"] = hhmm
        out["_decision_price"] = {i: live["indices"][i]["last_price"] for i in live["indices"]}
        result["decisions"].append(out)
        dec = (out.get("decision") or "").upper()
        if dec in ("ENTER", "SKIP"):
            final = out
            break
        # collapsed-wait (variant B): one WAIT -> jump to its resolution and decide once more;
        # a 2nd WAIT is finalized as the decision (no further looping).
        if dec == "WAIT" and collapse_wait and len(result["decisions"]) >= 2:
            final = out
            break
        prior.append(_slim_decision(out, hhmm))
        try:
            step = max(1, min(int(out.get("recheck_in_minutes") or 1), 5))
        except (TypeError, ValueError):
            step = 1
        nxt = (datetime.combine(date.today(), cp) + timedelta(minutes=step)).time()
        cp = nxt if nxt < CALL2_DEADLINE else CALL2_DEADLINE
        if result["decisions"][-1]["_at"] == cp.strftime("%H:%M"):
            break
    result["final"] = final or (result["decisions"][-1] if result["decisions"]
                                else {"decision": "SKIP", "note": "no opening data"})
    json.dump(result, open(cache, "w"), indent=2)
    return result


# ── compute what actually happened + score the decision ──────────────────────
HOLD_MIN = 120  # he holds max ~2 hours — grade the move inside that window, not whole-day


def _add_min(hhmm: str, mins: int) -> str:
    tot = int(hhmm[:2]) * 60 + int(hhmm[3:5]) + mins
    return f"{tot // 60:02d}:{tot % 60:02d}"


def actuals_for(today_candles: list[dict], prev_close: float, from_hhmm: str | None) -> dict:
    o = float(today_candles[0]["open"])
    hi = max(float(c["high"]) for c in today_candles)
    lo = min(float(c["low"]) for c in today_candles)
    cl = float(today_candles[-1]["close"])
    gap = round((o / prev_close - 1) * 100, 2) if prev_close else None
    # forward path within the ~2-hour HOLD window from the decision time (entry proxy)
    start = from_hhmm or "09:18"
    end = _add_min(start, HOLD_MIN)
    fwd = [c for c in today_candles if start <= c["ts"][11:16] <= end]
    if fwd:
        fp = float(fwd[0]["open"])
        f_hi = max(float(c["high"]) for c in fwd)
        f_lo = min(float(c["low"]) for c in fwd)
        mfe_up = round((f_hi - fp) / fp * 100, 2)
        mfe_dn = round((fp - f_lo) / fp * 100, 2)
        net = round((cl - fp) / fp * 100, 2)
    else:
        mfe_up = mfe_dn = net = 0.0
    return {
        "open": round(o, 2), "high": round(hi, 2), "low": round(lo, 2), "close": round(cl, 2),
        "gap_pct": gap, "range_pct": round((hi - lo) / o * 100, 2),
        "open_to_close_pct": round((cl - o) / o * 100, 2),
        "fwd_up_pct": mfe_up, "fwd_down_pct": mfe_dn, "fwd_net_pct": net,
    }


def grade(final: dict, act: dict) -> tuple[str, str]:
    """Return (verdict, note). Engine-independent, ~0.4% favorable threshold for a long-option win."""
    dec = (final.get("decision") or "").upper()
    action = (final.get("action") or "").upper()
    direction = (final.get("direction") or "").upper()
    TH = 0.5  # % MFE in the ~2h hold window = a tradable bought-option move
    up, dn = act["fwd_up_pct"], act["fwd_down_pct"]
    if dec != "ENTER":
        # SKIP / unresolved WAIT — was there a clean ONE-SIDED move in the 2h window it passed on?
        if max(up, dn) >= TH and min(up, dn) < TH:  # clean directional thrust, not whipsaw
            side = "UP" if up >= dn else "DOWN"
            return "SKIP / missed", f"clean {side} {max(up, dn):.1f}% in the 2h window went untraded"
        if min(up, dn) >= TH:
            return "SKIP / good", f"two-sided chop (±{up:.1f}/{dn:.1f}% both ways) — correct skip"
        return "SKIP / good", f"no {TH}% move in 2h (±{up:.1f}/{dn:.1f}%) — correct skip"
    if action == "SELL":
        return "SELL", f"range day check: actual range {act['range_pct']:.1f}% (seller wins if stays inside expected band)"
    if direction == "CE":
        if act["fwd_up_pct"] >= TH and act["fwd_up_pct"] >= act["fwd_down_pct"]:
            return "CE / RIGHT", f"+{act['fwd_up_pct']:.1f}% up after entry"
        return "CE / WRONG", f"down {act['fwd_down_pct']:.1f}% / up only {act['fwd_up_pct']:.1f}% after entry"
    if direction == "PE":
        if act["fwd_down_pct"] >= TH and act["fwd_down_pct"] >= act["fwd_up_pct"]:
            return "PE / RIGHT", f"-{act['fwd_down_pct']:.1f}% down after entry"
        return "PE / WRONG", f"up {act['fwd_up_pct']:.1f}% / down only {act['fwd_down_pct']:.1f}% after entry"
    return "ENTER?", "no direction"


VARIANT_CFG = {
    "A": {"start": CALL2_START, "collapse": False},      # baseline: 09:18 start, recheck loop
    "B": {"start": time(9, 21), "collapse": True},        # activated: 5-6 candle read, collapsed wait
    "C": {"start": CALL2_START, "collapse": False},       # A harness + the 2 calibration fixes (prompt only)
}


async def run(start: date, end: date, model: str, refresh: bool,
              variant: str = "A", tag: str | None = None) -> None:
    db = os.environ.get("DATABASE_URL", DEFAULT_DB).replace("+asyncpg", "")
    conn = await asyncpg.connect(db)
    token = _resolve_token()
    cfg = VARIANT_CFG[variant.upper()]
    sim_dir = f"/tmp/ih_sim_{tag or variant.upper()}"
    try:
        days = [r["d"] for r in await conn.fetch(
            """SELECT DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date d
               FROM market_data_1m WHERE symbol='NIFTY'
                 AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date BETWEEN $1 AND $2
               ORDER BY d""", start, end)]
        rows = []
        for d in days:
            res = await decide_day(conn, d, model, token, refresh, variant=variant.upper(),
                                   sim_dir=sim_dir, start_time=cfg["start"],
                                   collapse_wait=cfg["collapse"])
            final = res.get("final") or {}
            lead = await _fetch_day(conn, LEAD, d)
            pc = await _prev_trading_date(conn, LEAD, d)
            prev = await _fetch_day(conn, LEAD, pc) if pc else []
            prev_close = float(prev[-1]["close"]) if prev else 0.0
            at = final.get("_at")
            act = actuals_for(lead, prev_close, at) if lead else {}
            verdict, note = grade(final, act) if act else ("n/a", "no data")
            c1 = res.get("call1") or {}
            rows.append({
                "date": d.isoformat(),
                "lean": f"{c1.get('regime_lean','?')}/{c1.get('preferred_action_lean','?')}",
                "decision": final.get("decision"), "action": final.get("action"),
                "direction": final.get("direction"), "conf": final.get("confidence"),
                "at": at, "n_checks": len(res.get("decisions", [])),
                "gap": act.get("gap_pct"), "o2c": act.get("open_to_close_pct"),
                "range": act.get("range_pct"), "verdict": verdict, "note": note,
            })

        # ── report ──
        print(f"\n{'='*120}\nINTRADAY HUNTER SIM  variant={variant.upper()}  {start} → {end}  model={model}\n{'='*120}")
        hdr = f"{'DATE':11} {'C1 lean':18} {'DECISION':22} {'cnf':>3} {'@':>5} {'gap%':>6} {'o→c%':>6} {'rng%':>5}  VERDICT"
        print(hdr); print("-" * 120)
        for r in rows:
            dec = f"{r['decision'] or '?'} {r.get('action') or ''} {r.get('direction') or ''}".strip()
            print(f"{r['date']:11} {r['lean']:18} {dec:22} {str(r['conf'] or ''):>3} "
                  f"{str(r['at'] or '-'):>5} {_n(r['gap'])} {_n(r['o2c'])} {_n(r['range'],5)}  {r['verdict']}")
            print(f"{'':54}└─ {r['note']}")
        # tally
        from collections import Counter
        tally = Counter(r["verdict"] for r in rows)
        print("-" * 120)
        print("TALLY:", dict(tally))
    finally:
        await conn.close()


def _n(v, w=6):
    return (f"{v:+.2f}" if isinstance(v, (int, float)) else "-").rjust(w)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--model", default="claude-opus-4-8")
    p.add_argument("--refresh", action="store_true", help="ignore cache, recompute LLM calls")
    p.add_argument("--variant", default="A", choices=["A", "B", "C", "a", "b", "c"],
                   help="A=baseline; B=activated (09:21, collapsed wait); C=A harness + 2 calibration fixes")
    p.add_argument("--tag", default=None, help="cache/output namespace (default = variant)")
    a = p.parse_args()
    asyncio.run(run(datetime.strptime(a.start, "%Y-%m-%d").date(),
                    datetime.strptime(a.end, "%Y-%m-%d").date(), a.model, a.refresh,
                    variant=a.variant, tag=a.tag))


if __name__ == "__main__":
    main()
