"""Call 2 — the at-open decision (watcher-triggered, 09:15-09:30 IST).

Takes the Call 1 thesis (verbatim) + the live open/gap/first-candles + this session's prior
decisions, renders the live opening chart per index, calls Claude (variant D), parses the
full-plan JSON, and persists it onto the day's run row (latest decision + audit history +
denormalized decision/direction/confidence). A parse/LLM failure is treated as SKIP.

Backend-session twin of `scripts/intraday_hunter/prototype_agent.py`'s Call 2 loop body.
"""
from __future__ import annotations

import logging
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_ist
from app.models.intraday_hunter_run import IntradayHunterRun
from app.services.intraday_hunter import charts, context, llm_cli, prompts, store, thesis
from app.services.intraday_hunter.data import CHART_DIR, fetch_day, fetch_vix

logger = logging.getLogger(__name__)

_LEVEL_KEYS = ("prev_close", "pdh", "pdl", "round")
_CALL2_REQUIRED = ("decision", "confidence")
MARKET_OPEN_MIN = 9 * 60 + 15


def _valid_thesis(run: IntradayHunterRun | None) -> bool:
    """True when the run carries a usable Call 1 thesis (not a failure stub)."""
    return bool(run and run.call1_json and not run.call1_json.get("error"))


def _slim_decision(out: dict) -> dict:
    """Compact prior-decision record fed back into the next recheck (no charts/prices)."""
    return {
        "at": out.get("_at"),
        "decision": out.get("decision"),
        "direction": out.get("direction"),
        "entry_trigger": out.get("entry_trigger"),
        "confidence": out.get("confidence"),
        "recheck_in_minutes": out.get("recheck_in_minutes"),
    }


def _coerce_int(v) -> int | None:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


async def run_call2(
    session: AsyncSession,
    trading_date: date,
    *,
    now: time | None = None,
    variant: str = "D",
    model: str = llm_cli.MODEL,
    token: str | None = None,
) -> IntradayHunterRun | None:
    """Run one Call 2 for `trading_date` as of `now` (IST) and persist the decision.

    Returns the updated run, or None if it could not run (no thesis, or no opening data yet).
    """
    now = now or now_ist().time()
    hhmm = now.strftime("%H:%M")
    ds = trading_date.isoformat()

    run = await store.get_run(session, trading_date)
    if not _valid_thesis(run):
        logger.warning("intraday_hunter: Call 2 @ %s skipped — no thesis for %s", hhmm, ds)
        return run

    # ── per-index prev-day structure + (re-rendered) prev-day charts ──
    per_index_prev, prevday_paths = await thesis.prepare_prev_day(session, trading_date)
    if not per_index_prev:
        logger.warning("intraday_hunter: Call 2 @ %s — no prev-day data for %s", hhmm, ds)
        return run

    # ── live opening state up to `now`, opening chart per index ──
    per_open: dict[str, float] = {}
    per_opening: dict[str, list[dict]] = {}
    opening_paths: dict[str, str] = {}
    for idx in per_index_prev:
        day = await fetch_day(session, idx, trading_date)
        opening = [c for c in day if c["ts"][11:16] <= hhmm]
        if not opening:
            continue
        per_open[idx] = float(opening[0]["open"])
        per_opening[idx] = opening
        levels = {k: per_index_prev[idx][k] for k in _LEVEL_KEYS}
        try:
            opening_paths[idx] = charts.render_opening_chart(
                idx, opening, levels, CHART_DIR, ds
            )
        except Exception:  # noqa: BLE001 — a chart failure must not abort the decision
            logger.exception("intraday_hunter: opening chart render failed for %s", idx)

    if not per_open:
        logger.info("intraday_hunter: Call 2 @ %s — no opening data yet for %s", hhmm, ds)
        return run

    mins = (now.hour * 60 + now.minute) - MARKET_OPEN_MIN
    vix = await fetch_vix(session, trading_date, upto=now)
    live = context.build_call2_live(
        per_open, per_index_prev, per_opening, hhmm, mins, india_vix=vix
    )

    prior = [_slim_decision(d) for d in (run.call2_history or [])]
    prompt = (
        prompts.build_system_prompt(variant)
        + "\n\n"
        + prompts.build_call2_prompt(run.call1_json, live, prior)
    )

    out = await llm_cli.call_claude_json(
        prompt,
        image_paths=list(prevday_paths.values()) + list(opening_paths.values()),
        required_keys=_CALL2_REQUIRED,
        token=token,
        model=model,
    )
    if not out:
        out = {"decision": "SKIP", "note": "call2 failed -> SKIP", "confidence": 0}

    # audit stamps (mirrors the offline simulator's per-decision record)
    out["_at"] = hhmm
    out["_decision_price"] = {i: live["indices"][i]["last_price"] for i in live["indices"]}

    decision = (out.get("decision") or "SKIP").upper()
    run.call1_chart_paths = prevday_paths or run.call1_chart_paths
    run.call2_json = out
    run.call2_history = (run.call2_history or []) + [out]
    run.call2_chart_paths = {"prevday": prevday_paths, "opening": opening_paths}
    run.decision = decision
    run.direction = (out.get("direction") or None) if decision == "ENTER" else None
    run.confidence = _coerce_int(out.get("confidence"))
    run.status = decision if decision in ("ENTER", "WAIT", "SKIP") else "WATCHING"

    logger.info(
        "intraday_hunter: Call 2 @ %s for %s -> %s %s (conf=%s)",
        hhmm, ds, decision, run.direction or "", run.confidence,
    )

    # On ENTER, emit tradeable option signals (shadow + paper-YOLO pick them up). A failure
    # here must never undo the decision record, so it is isolated.
    if decision == "ENTER":
        try:
            from app.services.intraday_hunter.signals import emit_signals_for_enter
            emitted = await emit_signals_for_enter(session, run, live)
            if emitted:
                logger.info("intraday_hunter: emitted %d signal(s) for %s: %s",
                            len(emitted), ds, ", ".join(emitted))
        except Exception:  # noqa: BLE001
            logger.exception("intraday_hunter: signal emission failed for %s (decision stands)", ds)

    return run
