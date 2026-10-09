"""The learning dataset writer — one `ih_minute_log` row per index per minute.

Driven by the NIFTY 1m candle close (feed_manager hook, fire-and-forget, failure-isolated:
logging must never break trading). Writes for candle minutes 09:15–10:45 IST
(`minute_log_start`/`minute_log_end`) AND every minute while any v2 position is open.

features (per index): the minute's OHLC, the §3.2 stop-level facts, opening type, opening OI flow
so far, order-flow aggregates (index future + that index's ATM CE/PE), India VIX, the teacher's
side for today's opening.
arms (what each candidate would decide THIS minute, even when not trading):
  rule_a (ride PDH/PDL break on ≥2 of 3), plan_side, oi_flow_side, v1_state (latest v1 decision),
  v2_llm (latest v2 Call 2), gates (plan / OI verdicts for the v2 and rule_a sides), jev.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.redis import get_cached_price
from app.models.ih_v2 import IhMinuteLog
from app.models.position import Position
from app.services.intraday_hunter import store
from app.services.intraday_hunter_v2 import capture, jev, levels, oi_flow
from app.services.intraday_hunter_v2 import context as ctx_mod
from app.services.intraday_hunter_v2 import gates as gates_mod
from app.services.intraday_hunter_v2.orderflow import orderflow_tracker
from app.services.intraday_hunter_v2.params import STRATEGY, VARIANT, parse_hhmm, v2_active, v2_params_async

logger = logging.getLogger(__name__)

SETTLE_S = 4.0  # let the other indices' candles for this minute persist first


def _slim_run(run) -> dict | None:
    if run is None:
        return None
    c2 = run.call2_json or {}
    return {"status": run.status, "decision": run.decision, "direction": run.direction,
            "confidence": run.confidence, "at": c2.get("_at"),
            "pool_broken": c2.get("pool_broken"), "skip_reason_code": c2.get("skip_reason_code")}


def _atm_contracts(cap: dict, index: str, spot: float | None) -> dict[str, str]:
    """{CE: sym, PE: sym} for the captured strike nearest `spot`."""
    rows = (cap.get("contracts") or {}).get(index) or []
    if not rows or not spot:
        return {}
    best = min({r["strike"] for r in rows}, key=lambda k: abs(float(k) - spot))
    return {r["type"]: r["symbol"] for r in rows if r["strike"] == best}


def build_arms(
    facts: dict[str, dict], plan_side: str | None, flow: dict, v1_run, v2_run, params: dict,
) -> dict:
    """Every candidate arm's call for this minute (pure apart from the passed objects)."""
    rule_a = levels.rule_a_side(facts)
    v2 = _slim_run(v2_run)
    v2_side = (v2 or {}).get("direction")
    oi_side = flow.get("side")
    enf = {"enforce_plan": bool(params.get("enforce_plan_gate")),
           "enforce_oi": bool(params.get("enforce_oi_gate"))}
    return {
        "rule_a": rule_a,
        "plan_side": plan_side,
        "oi_flow_side": oi_side,
        "v1_state": _slim_run(v1_run),
        "v2_llm": v2,
        "gates": {
            "v2": gates_mod.compute_gates(v2_side, plan_side, oi_side, **enf) if v2_side else None,
            "rule_a": gates_mod.compute_gates(rule_a, plan_side, oi_side, **enf) if rule_a else None,
        },
    }


async def _v2_position_open(session) -> bool:
    n = (await session.execute(
        select(func.count(Position.id)).where(Position.strategy_name == STRATEGY)
    )).scalar() or 0
    return n > 0


async def log_minute(candle_ts: datetime, *, settle_s: float = SETTLE_S) -> int:
    """Write this minute's rows (one per index). Returns rows written; never raises."""
    try:
        return await _log_minute(candle_ts, settle_s)
    except Exception:  # noqa: BLE001 — the learning log must never disturb trading
        logger.exception("ih_v2: minute log failed for %s", candle_ts)
        return 0


async def _log_minute(candle_ts: datetime, settle_s: float) -> int:
    if not await v2_active():
        return 0
    ts = (candle_ts.astimezone(IST) if candle_ts.tzinfo else candle_ts.replace(tzinfo=IST))
    ts = ts.replace(second=0, microsecond=0)
    d, minute = ts.date(), ts.time()
    params = await v2_params_async()
    in_window = parse_hhmm(params["minute_log_start"]) <= minute <= parse_hhmm(params["minute_log_end"])

    async with async_session_factory() as session:
        in_position = await _v2_position_open(session)
        if not (in_window or in_position):
            return 0
        if settle_s:
            await asyncio.sleep(settle_s)
        prev = await ctx_mod.prev_day_all(session, d)
        candles = await ctx_mod.today_candles(session, d, minute)
        teacher = await ctx_mod.get_teacher_day(session, d)
        plan = teacher.plan if teacher and teacher.plan else None
        facts = ctx_mod.facts_by_index(prev, candles, plan, params)
        if not facts:
            return 0
        opening = ctx_mod.opening_of(facts, params)
        plan_side = ctx_mod.plan_side_today(plan, opening)
        decision_t = (ts + timedelta(minutes=1)).time()
        flow = await oi_flow.opening_oi_flow(
            session, d, list(levels.INDICES), now=decision_t,
            deadband=float(params.get("oi_flow_deadband", 0.0)),
        )
        v1_run = await store.get_run(session, d, "v1")
        v2_run = await store.get_run(session, d, VARIANT)
        vix = await ctx_mod.vix_now(session, d, upto=minute)
        arms = build_arms(facts, plan_side, flow, v1_run, v2_run, params)

        jev_window = parse_hhmm(params["call2_first"]) <= decision_t <= parse_hhmm(params["call2_deadline"])
        if jev.is_enabled() and (jev_window or in_position):
            state = {"time": decision_t.strftime("%H:%M"), "opening_type": opening,
                     "facts": [ln for f in facts.values() for ln in levels.describe_facts(f)],
                     "teacher_side_for_today": plan_side, "oi_flow_side": flow.get("side")}
            if in_position and v2_run and v2_run.direction:
                state["open_basket_direction"] = v2_run.direction
            arms["jev"] = await jev.ask(state, in_position=in_position)
        else:
            arms["jev"] = None

        cap = await capture.load_capture(d)
        # Re-assert tracking from the day's capture (idempotent) so a mid-day backend restart,
        # which empties the in-memory tracker, resumes order flow from the next minute.
        orderflow_tracker.track(list(cap.get("symbols") or []) + [f"{i}_FUT" for i in facts])
        hhmm = minute.strftime("%H:%M")
        rows = []
        for idx, f in facts.items():
            c = next((x for x in candles.get(idx, []) if x["ts"][11:16] == hhmm), None)
            spot = (await get_cached_price(idx) or {}).get("ltp")
            atm = _atm_contracts(cap, idx, float(spot) if spot else f["last"])
            features = {
                "ohlc": ({k: float(c[k]) for k in ("open", "high", "low", "close")} if c else None),
                "level_facts": f,
                "opening_type": opening,
                "oi_flow": flow,
                "order_flow": {
                    "fut": orderflow_tracker.snapshot(f"{idx}_FUT", hhmm),
                    "atm_ce": orderflow_tracker.snapshot(atm["CE"], hhmm) if atm.get("CE") else None,
                    "atm_pe": orderflow_tracker.snapshot(atm["PE"], hhmm) if atm.get("PE") else None,
                    "atm_symbols": atm,
                },
                "india_vix": vix,
                "plan_side": plan_side,
                "teacher_plan_missing": plan is None,
                "in_position": in_position,
            }
            rows.append({"trading_date": d, "minute_ts": ts, "index": idx, "variant": VARIANT,
                         "features": features, "arms": arms})
        stmt = pg_insert(IhMinuteLog).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ih_minute_log",
            set_={"features": stmt.excluded.features, "arms": stmt.excluded.arms,
                  "updated_at": func.now()},
        )
        await session.execute(stmt)
        await session.commit()
    logger.debug("ih_v2: minute log %s %s — %d rows", d, hhmm, len(rows))
    return len(rows)
