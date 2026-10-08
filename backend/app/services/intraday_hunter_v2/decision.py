"""v2 Call 2 — the fast, text-only at-open decision (09:16 → every minute → 09:25 deadline).

Builds precomputed live facts (stop-level facts per index in plain words, opening type, OI flow
so far, order-flow aggregates, the teacher's side for today's opening, VIX) — no charts — calls the
configurable Call 2 model (`call2_model`, Sonnet-class by default; latency logged per call,
target < 45s), applies the shadow-only gates, persists onto the v2 run row, and on ENTER emits the
basket signals.

Failure handling differs from v1 on purpose: a failed/unparseable call BEFORE the deadline is a
WAIT (re-checked next minute); at the deadline it is a SKIP. A WAIT at the deadline becomes a SKIP
with skip_reason_code NO_POOL_BROKEN_BY_DEADLINE.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IST
from app.core.redis import get_cached_price
from app.models.intraday_hunter_run import IntradayHunterRun
from app.services.intraday_hunter import llm_cli, store
from app.services.intraday_hunter_v2 import context as ctx_mod
from app.services.intraday_hunter_v2 import gates as gates_mod
from app.services.intraday_hunter_v2 import levels, oi_flow, prompts
from app.services.intraday_hunter_v2.orderflow import orderflow_tracker
from app.services.intraday_hunter_v2.params import VARIANT, call2_model, parse_hhmm, v2_params_async

logger = logging.getLogger(__name__)

_REQUIRED = ("decision", "confidence")
MARKET_OPEN_MIN = 9 * 60 + 15


def _minus_minute(t: time) -> time:
    dt = datetime.combine(date(2000, 1, 1), t) - timedelta(minutes=1)
    return dt.time()


def _coerce_int(v) -> int | None:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def normalize_call2(out: dict | None, *, at_deadline: bool) -> dict:
    """Validate/normalize a Call 2 reply into a final record (pure; unit-tested).

    - None (LLM failure) → WAIT before the deadline, SKIP(OTHER) at it.
    - ENTER without a CE/PE direction → WAIT (or SKIP at the deadline).
    - SKIP without a valid skip_reason_code → 'OTHER'.
    - WAIT at the deadline → SKIP(NO_POOL_BROKEN_BY_DEADLINE).
    """
    if not out:
        out = {"decision": "WAIT", "confidence": 0, "note": "call2 failed/unparseable",
               "_llm_failed": True}
    rec = dict(out)
    dec = str(rec.get("decision") or "").upper()
    if dec not in ("ENTER", "WAIT", "SKIP"):
        dec = "WAIT"
    direction = str(rec.get("direction") or "").upper() or None
    if dec == "ENTER" and direction not in ("CE", "PE"):
        dec = "WAIT"
        rec["note"] = "ENTER without a CE/PE direction → treated as WAIT"
    if dec == "WAIT" and at_deadline:
        dec = "SKIP"
        rec["skip_reason_code"] = ("OTHER" if rec.get("_llm_failed")
                                   else "NO_POOL_BROKEN_BY_DEADLINE")
    if dec == "SKIP" and rec.get("skip_reason_code") not in prompts.SKIP_REASON_CODES:
        rec["skip_reason_code"] = "OTHER"
    if dec != "SKIP":
        rec["skip_reason_code"] = None
    rec["decision"] = dec
    rec["direction"] = direction if dec == "ENTER" else None
    return rec


def _slim(out: dict) -> dict:
    return {k: out.get(k) for k in ("_at", "decision", "direction", "pool_broken",
                                     "next_pool_target", "confidence", "skip_reason_code")}


async def _live_spot(index: str) -> float | None:
    d = await get_cached_price(index)
    try:
        v = float((d or {}).get("ltp") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


async def build_live(session: AsyncSession, d: date, now: time, params: dict) -> dict | None:
    """Live Call 2 inputs as of decision time `now` (candles through the minute before)."""
    candle_upto = _minus_minute(now)
    prev = await ctx_mod.prev_day_all(session, d)
    candles = await ctx_mod.today_candles(session, d, candle_upto)
    teacher = await ctx_mod.get_teacher_day(session, d)
    plan = teacher.plan if teacher and teacher.plan else None
    facts = ctx_mod.facts_by_index(prev, candles, plan, params)
    if not facts:
        return None
    opening = ctx_mod.opening_of(facts, params)
    flow = await oi_flow.opening_oi_flow(
        session, d, list(levels.INDICES), now=now,
        deadband=float(params.get("oi_flow_deadband", 0.0)),
    )
    minute = candle_upto.strftime("%H:%M")
    ofl = {s: orderflow_tracker.snapshot(s, minute)
           for s in sorted(orderflow_tracker.tracked)
           if s.endswith("_FUT") or s.endswith("CE") or s.endswith("PE")}
    ofl = {k: v for k, v in ofl.items() if v}
    plan_side = ctx_mod.plan_side_today(plan, opening)
    text: list[str] = [f"Opening type: {opening.replace('_', '-')}."]
    for f in facts.values():
        text.extend(levels.describe_facts(f))
    return {
        "now": now.strftime("%H:%M"),
        "deadline": params.get("call2_deadline"),
        "minutes_since_open": now.hour * 60 + now.minute - MARKET_OPEN_MIN,
        "opening_type": opening,
        "facts_text": text,
        "pdh_pdl_break_by_index": {i: levels.pdh_pdl_break_side(f) for i, f in facts.items()},
        "pools_taken": {i: f["pools_taken"] for i, f in facts.items()},
        "teacher": {
            "plan_missing": plan is None,
            "side_for_todays_opening": plan_side,
            "bias": (plan or {}).get("bias"),
            "summary": (plan or {}).get("summary"),
        },
        "oi_flow_so_far": {"flow": flow.get("flow"), "side": flow.get("side"),
                           "per_index": flow.get("per_index")},
        "order_flow_last_minute": ofl or "not available",
        "india_vix": await ctx_mod.vix_now(session, d, upto=now),
        "_facts": facts,
        "_plan_side": plan_side,
    }


async def run_call2(
    session: AsyncSession, d: date, *, now: time, t_hook: float | None = None,
    token: str | None = None,
) -> IntradayHunterRun | None:
    """One v2 Call 2 at decision time `now` (IST wall clock). Returns the run (None: no data yet).

    `t_hook` = time.monotonic() at the candle-close hook, so the logged latency covers the
    whole hook→decision path, not just the model call.
    """
    params = await v2_params_async()
    deadline = parse_hhmm(params["call2_deadline"])
    at_deadline = now >= deadline
    run = await store.get_or_create_run(session, d, VARIANT)
    t_start = t_hook if t_hook is not None else _time.monotonic()

    live = await build_live(session, d, now, params)
    if live is None:
        logger.info("ih_v2: Call 2 @ %s — no opening data yet", now.strftime("%H:%M"))
        if at_deadline:
            out = normalize_call2({"decision": "SKIP", "skip_reason_code": "DATA_MISSING",
                                   "confidence": 0}, at_deadline=True)
            out["_at"] = now.strftime("%H:%M")
            _persist(run, out)
            return run
        return None
    facts = live.pop("_facts")
    plan_side = live.pop("_plan_side")

    call1 = run.call1_json if run.call1_json and not run.call1_json.get("error") else None
    prior = [_slim(x) for x in (run.call2_history or [])]
    prompt = prompts.SYSTEM_PROMPT_V2 + "\n\n" + prompts.build_call2_prompt(call1, live, prior)
    model = call2_model(params)
    t_llm = _time.monotonic()
    raw = await llm_cli.call_claude_json(
        prompt, required_keys=_REQUIRED, token=token, model=model,
        timeout_s=int(params.get("call2_timeout_s", 60)),
    )
    t_end = _time.monotonic()
    out = normalize_call2(raw, at_deadline=at_deadline)
    out["_at"] = now.strftime("%H:%M")
    out["_model"] = model
    out["_latency_ms"] = int((t_end - t_llm) * 1000)
    out["_hook_to_decision_ms"] = int((t_end - t_start) * 1000)
    out["_opening_type"] = live["opening_type"]
    out["_decision_price"] = {i: f["last"] for i, f in facts.items()}
    out["_teacher_plan_missing"] = live["teacher"]["plan_missing"]
    out["_oi_flow"] = live["oi_flow_so_far"]
    gates = gates_mod.compute_gates(
        out.get("direction") or None, plan_side, live["oi_flow_so_far"].get("side"),
        enforce_plan=bool(params.get("enforce_plan_gate")),
        enforce_oi=bool(params.get("enforce_oi_gate")),
    )
    out["_gates"] = gates
    if out["decision"] == "ENTER" and gates["blocked"]:
        out["_gate_blocked_side"] = out["direction"]
        out["decision"], out["direction"], out["skip_reason_code"] = "SKIP", None, "OTHER"
    _persist(run, out)
    logger.info(
        "ih_v2: Call 2 @ %s for %s -> %s %s (conf=%s, model=%s, llm=%dms, hook→decision=%dms)",
        out["_at"], d, out["decision"], out.get("direction") or "", out.get("confidence"),
        model, out["_latency_ms"], out["_hook_to_decision_ms"],
    )

    if out["decision"] == "ENTER":
        try:
            from app.services.intraday_hunter_v2.signals import emit_signals_for_enter
            spots = {}
            for i, f in facts.items():
                spots[i] = (await _live_spot(i)) or f["last"]
            emitted = await emit_signals_for_enter(session, run, spots, params)
            run.call2_json = {**run.call2_json, "_emitted": emitted}
            logger.info("ih_v2: emitted %d leg(s) for %s: %s", len(emitted), d, ", ".join(emitted))
        except Exception:  # noqa: BLE001 — never undo the decision record
            logger.exception("ih_v2: signal emission failed for %s (decision stands)", d)
    return run


def _persist(run: IntradayHunterRun, out: dict) -> None:
    run.call2_json = out
    run.call2_history = (run.call2_history or []) + [out]
    run.decision = out["decision"]
    run.direction = out.get("direction")
    run.confidence = _coerce_int(out.get("confidence"))
    run.status = out["decision"]


def decision_time_for_candle(candle_ts: datetime) -> time:
    """The wall-clock decision time a candle close drives: candle minute + 1 (09:15 → 09:16)."""
    ts = candle_ts.astimezone(IST) if candle_ts.tzinfo else candle_ts
    return (ts.replace(second=0, microsecond=0) + timedelta(minutes=1)).time()
