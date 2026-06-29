"""Call 1 — the pre-open thesis (~08:45 IST).

Builds the per-index previous-day structure + multi-day STRUCTURAL memory + India VIX +
calendar, renders one prev-day chart per index, calls Claude (variant D, the promoted
baseline = C + the VIX-regime fix), and persists the thesis onto the day's `intraday_hunter_runs` row.

This is the backend-session twin of `scripts/intraday_hunter/prototype_agent.py`'s Call 1
block — same context builders, same prompts, same chart renderers, same llm_cli wrapper.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.intraday_hunter import charts, context, llm_cli, prompts, store
from app.services.intraday_hunter.data import (
    CHART_DIR,
    compute_calendar,
    fetch_day,
    fetch_vix,
    prev_trading_date,
)
from app.models.intraday_hunter_run import IntradayHunterRun

logger = logging.getLogger(__name__)

_LEVEL_KEYS = ("prev_close", "pdh", "pdl", "round")
_CALL1_REQUIRED = ("trapped_side", "thesis", "conditional_plan")


async def prepare_prev_day(
    session: AsyncSession, trading_date: date
) -> tuple[dict[str, dict], dict[str, str]]:
    """Per-index previous-day structure + rendered prev-day chart paths.

    Shared by Call 1 (thesis) and Call 2 (decision) so both see identical structure/levels
    and the same prev-day images. Indices with no prior-day data are silently skipped.
    """
    ds = trading_date.isoformat()
    per_index_prev: dict[str, dict] = {}
    chart_paths: dict[str, str] = {}
    for idx in context.INDICES:
        pd = await prev_trading_date(session, idx, trading_date)
        if pd is None:
            continue
        prev_candles = await fetch_day(session, idx, pd)
        if not prev_candles:
            continue
        struct = context.prev_day_structure(prev_candles, idx)
        struct["prev_date"] = pd.isoformat()
        per_index_prev[idx] = struct
        levels = {k: struct[k] for k in _LEVEL_KEYS}
        try:
            chart_paths[idx] = charts.render_prev_day_chart(
                idx, prev_candles, levels, CHART_DIR, ds
            )
        except Exception:  # noqa: BLE001 — a chart failure must not abort the thesis
            logger.exception("intraday_hunter: prev-day chart render failed for %s", idx)
    return per_index_prev, chart_paths


async def run_call1(
    session: AsyncSession,
    trading_date: date,
    *,
    variant: str = "D",
    model: str = llm_cli.MODEL,
    token: str | None = None,
) -> IntradayHunterRun:
    """Run Call 1 for `trading_date` and persist the thesis. Returns the run row.

    On any failure to assemble context or get a thesis, the day is finalized as SKIP
    (fail-safe — a missing thesis never produces a bogus plan).
    """
    run = await store.get_or_create_run(session, trading_date)
    ds = trading_date.isoformat()

    # ── per-index previous-day structure + prev-day charts ──
    per_index_prev, chart_paths = await prepare_prev_day(session, trading_date)

    if not per_index_prev:
        logger.warning("intraday_hunter: no previous-day data for %s — SKIP", ds)
        _finalize_skip(run, "no previous-day data available")
        return run

    # ── volatility + calendar + multi-day memory ──
    prev_date = next(iter(per_index_prev.values()))["prev_date"]
    vix = await fetch_vix(session, date.fromisoformat(prev_date))
    cal = compute_calendar(trading_date)
    memory = store.build_memory_snapshot(await store.recent_runs(session, trading_date, 3))

    c1_ctx = context.build_call1_context(per_index_prev, memory, cal, india_vix=vix)
    prompt = prompts.build_system_prompt(variant) + "\n\n" + prompts.build_call1_prompt(c1_ctx)

    c1 = await llm_cli.call_claude_json(
        prompt,
        image_paths=list(chart_paths.values()),
        required_keys=_CALL1_REQUIRED,
        token=token,
        model=model,
    )

    run.call1_chart_paths = chart_paths
    run.is_expiry = bool(cal["is_expiry"])
    run.expiry_index = cal["expiry_index"]

    if not c1:
        logger.warning("intraday_hunter: Call 1 produced no thesis for %s — SKIP", ds)
        _finalize_skip(run, "Call 1 produced no thesis (LLM unavailable or invalid)")
        return run

    run.call1_json = c1
    run.status = "THESIS_READY"
    logger.info(
        "intraday_hunter: Call 1 thesis for %s — trapped=%s lean=%s/%s",
        ds, c1.get("trapped_side"), c1.get("regime_lean"), c1.get("preferred_action_lean"),
    )
    return run


def _finalize_skip(run: IntradayHunterRun, reason: str) -> None:
    """Terminal SKIP for the day (e.g. Call 1 failed) — the watcher will not fire Call 2."""
    run.call1_json = {"error": reason}
    run.status = "SKIP"
    run.decision = "SKIP"
    run.direction = None
    run.confidence = None
