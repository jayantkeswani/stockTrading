"""v2 Call 1 — the pre-open thesis (08:45 IST), on the `variant='v2'` run row.

Same base as v1 (prev-day structure + prev-day charts + India VIX + calendar) PLUS the teacher's
evening plan + his drawn levels, the computed pre-open stop-level list, and the last N graded
LESSONS (replacing v1's never-filled `thesis_played_out` memory). Separate prompt (v2 prompts.py).

Unlike v1, a failed Call 1 does NOT finalize the day as SKIP: v2's default is to trade the
morning, and its text-only Call 2 runs on live facts with or without a thesis.
"""
from __future__ import annotations

import logging
import os
import time as _time
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intraday_hunter_run import IntradayHunterRun
from app.services.intraday_hunter import charts, llm_cli, store
from app.services.intraday_hunter.data import CHART_DIR, compute_calendar, fetch_day
from app.services.intraday_hunter_v2 import context as ctx_mod
from app.services.intraday_hunter_v2 import prompts
from app.services.intraday_hunter_v2.params import VARIANT, v2_params_async

logger = logging.getLogger(__name__)

V2_CHART_DIR = os.path.join(CHART_DIR, "v2")  # under CHART_DIR so the chart API path guard holds
_CALL1_REQUIRED = ("bias", "thesis")


async def _render_prev_charts(session: AsyncSession, prev: dict[str, dict], d: date) -> dict[str, str]:
    paths: dict[str, str] = {}
    for idx, st in prev.items():
        try:
            candles = await fetch_day(session, idx, date.fromisoformat(st["prev_date"]))
            lv = {k: st[k] for k in ("prev_close", "pdh", "pdl", "round")}
            paths[idx] = charts.render_prev_day_chart(idx, candles, lv, V2_CHART_DIR, d.isoformat())
        except Exception:  # noqa: BLE001 — a chart failure must not abort the thesis
            logger.exception("ih_v2: prev-day chart failed for %s", idx)
    return paths


async def run_call1(session: AsyncSession, d: date, *, token: str | None = None) -> IntradayHunterRun:
    """Run v2 Call 1 for `d` and persist it on the v2 run row. Returns the run."""
    params = await v2_params_async()
    run = await store.get_or_create_run(session, d, VARIANT)
    prev = await ctx_mod.prev_day_all(session, d)
    if not prev:
        run.call1_json = {"error": "no previous-day data available"}
        run.status = "NO_THESIS"
        return run

    teacher = await ctx_mod.get_teacher_day(session, d)
    plan = teacher.plan if teacher and teacher.plan else None
    lessons = await ctx_mod.recent_lessons(session, d, int(params.get("lessons_n", 8)))
    prev_date = date.fromisoformat(next(iter(prev.values()))["prev_date"])
    vix = await ctx_mod.vix_now(session, prev_date)
    cal = compute_calendar(d)
    chart_paths = await _render_prev_charts(session, prev, d)

    c1_ctx = {
        "as_of": "pre-market",
        "indices": prev,
        "stop_pools_preopen": ctx_mod.preopen_levels(prev, plan, params),
        "teacher_plan": plan or "MISSING — no evening plan today (trade on the tape alone)",
        "india_vix": vix,
        "calendar": cal,
        "graded_lessons_recent": lessons or "none yet",
    }
    prompt = prompts.SYSTEM_PROMPT_V2 + "\n\n" + prompts.build_call1_prompt(c1_ctx)
    t0 = _time.monotonic()
    c1 = await llm_cli.call_claude_json(
        prompt, image_paths=list(chart_paths.values()), required_keys=_CALL1_REQUIRED,
        token=token, model=params.get("call1_model") or llm_cli.MODEL,
    )
    latency_ms = int((_time.monotonic() - t0) * 1000)
    # A lazy Call 1 can finish after Call 2 already decided: never clobber a decision status.
    await session.refresh(run, ["status", "decision"])
    decided = run.decision is not None
    run.call1_chart_paths = chart_paths
    run.is_expiry = bool(cal["is_expiry"])
    run.expiry_index = cal["expiry_index"]
    meta = {"_latency_ms": latency_ms, "_teacher_plan_missing": plan is None,
            "_lessons_n": len(lessons)}
    if not c1:
        logger.warning("ih_v2: Call 1 produced no thesis for %s (%dms) — Call 2 runs without it",
                       d, latency_ms)
        run.call1_json = {"error": "Call 1 produced no thesis", **meta}
        if not decided:
            run.status = "NO_THESIS"
        return run
    run.call1_json = {**c1, **meta}
    if not decided:
        run.status = "THESIS_READY"
    logger.info("ih_v2: Call 1 for %s — bias=%s latency=%dms plan_missing=%s",
                d, c1.get("bias"), latency_ms, plan is None)
    return run
