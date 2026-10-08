"""Intraday Hunter v2 — every scheduled job (IST), on its own APScheduler.

  00:00 / 06:00 / 08:00  teacher plan ingest ("Prediction For <date>")
  08:30                  plan-missing check → Telegram alert, v2 runs flagged teacher_plan_missing
  08:45                  v2 Call 1 (thesis)
  09:05                  reset the order-flow tracker for the day
  09:10 / 09:16          ATM±2 CE/PE capture (counterfactual premium paths), re-centred after the open
  09:20, then every 30m  index-candle presence check → alert once per index per day (the 2 Sep gap)
  09:30                  Call 2 decision check for v1 AND v2 → alert if either has no ENTER/SKIP
                         (these two reliability checks ignore the v2 kill switch — they also guard v1)
  13:00 / 15:00          teacher live-trade ingest; a success re-grades the day (→ FINAL)
  16:00                  nightly grade (PRELIM until the teacher's live trade lands)
  Sat 10:00              weekly review (proposal only — nothing auto-applied)
The 1-minute 09:15-09:45 OI window lives with the other OI jobs (oi_snapshot_task). Call 2 is
candle-driven (services/intraday_hunter_v2/watcher.py), not scheduled.

Every v2 job is gated on `_enabled_today()` = trading day AND `params.v2_active()` (the env flag +
the strategy_configs.is_active runtime kill switch).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=IST)
_alerted: set[tuple] = set()  # (date, kind, key) — one alert per kind per day

JOB_SCHEDULE = {
    "ih_v2_plan_0000": "00:00", "ih_v2_plan_0600": "06:00", "ih_v2_plan_0800": "08:00",
    "ih_v2_plan_missing_0830": "08:30", "ih_v2_call1_0845": "08:45",
    "ih_v2_orderflow_reset_0905": "09:05",
    "ih_v2_capture_0910": "09:10", "ih_v2_capture_0916": "09:16:30",
    "ih_v2_candle_check": "09:20 + every 30m to 15:20",
    "ih_v2_call2_check_0930": "09:30",
    "ih_v2_live_1300": "13:00", "ih_v2_live_1500": "15:00",
    "ih_v2_grade_1600": "16:00", "ih_v2_weekly_review": "Sat 10:00",
}


async def alert(kind: str, message: str, key: str = "") -> None:
    """Log + Telegram an operational alert, at most once per (day, kind, key)."""
    k = (now_ist().date(), kind, key)
    if k in _alerted:
        return
    _alerted.add(k)
    logger.warning("IH v2 ALERT [%s] %s", kind, message)
    try:
        from app.agent.notification import send_telegram
        await send_telegram(f"⚠️ <b>IH v2</b> {message}")
    except Exception:  # noqa: BLE001
        logger.exception("IH v2 alert Telegram failed")


async def _enabled_today() -> bool:
    """Env flag + the runtime kill switch (strategy_configs.is_active) + a trading day."""
    from app.services.intraday_hunter_v2.params import v2_active
    return is_trading_day(now_ist().date()) and await v2_active()


# ── teacher ──
async def plan_job(attempt: str) -> None:
    """Fetch the teacher's plan for today (posted the evening before)."""
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2.teacher import ingest
    try:
        await ingest.run_plan_job(now_ist().date(), attempt)
    except Exception as e:  # noqa: BLE001
        await alert("teacher_plan", f"plan job {attempt} crashed: {type(e).__name__}: {e}", attempt)


async def plan_missing_job() -> None:
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2.teacher import ingest
    try:
        await ingest.plan_missing_check(now_ist().date())
    except Exception:  # noqa: BLE001
        logger.exception("IH v2 plan-missing check failed")


async def live_job(attempt: str) -> None:
    """Fetch the teacher's actual live trade for today; re-grade on success."""
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2.teacher import ingest
    try:
        row = await ingest.run_live_job(now_ist().date(), attempt)
    except Exception as e:  # noqa: BLE001
        await alert("teacher_live", f"live job {attempt} crashed: {type(e).__name__}: {e}", attempt)
        return
    if row is not None and getattr(row, "live", None) and now_ist().hour >= 16:
        await grade_job()  # grade already ran — finalize it now that the live trade landed


# ── trading-day jobs ──
async def call1_job() -> None:
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2 import thesis
    try:
        async with async_session_factory() as session:
            run = await thesis.run_call1(session, now_ist().date())
            await session.commit()
        logger.info("IH v2 Call 1 — status=%s", run.status)
    except Exception:  # noqa: BLE001
        logger.exception("IH v2 Call 1 failed")


async def orderflow_reset_job() -> None:
    """Forget yesterday's order-flow buckets (always safe; no market access)."""
    from app.services.intraday_hunter_v2.orderflow import orderflow_tracker
    orderflow_tracker.reset()


async def capture_job(reason: str) -> None:
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2.capture import capture_atm_ladder
    try:
        await capture_atm_ladder(reason)
    except Exception as e:  # noqa: BLE001
        await alert("capture", f"ATM±2 capture ({reason}) failed: {type(e).__name__}: {e}", reason)


async def candle_check_job() -> None:
    """Alert when an index has no recent 1m candles in the current session (the 2 Sep gap)."""
    if not (settings.intraday_hunter_v2_enabled and is_trading_day(now_ist().date())):
        return
    from sqlalchemy import text
    now = now_ist()
    async with async_session_factory() as session:
        rows = (await session.execute(text(
            """
            SELECT symbol, MAX(timestamp) AS last_ts FROM market_data_1m
            WHERE symbol IN ('NIFTY','BANKNIFTY','SENSEX')
              AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
            GROUP BY symbol
            """
        ), {"d": now.date()})).all()
    last = {r.symbol: r.last_ts for r in rows}
    for idx in ("NIFTY", "BANKNIFTY", "SENSEX"):
        ts = last.get(idx)
        if ts is None:
            await alert("candles_missing", f"no {idx} 1m candles at all for {now.date()} "
                                           f"(as of {now:%H:%M})", idx)
        elif now - ts.astimezone(IST) > timedelta(minutes=10):
            await alert("candles_stale", f"{idx} 1m candles stale since "
                                         f"{ts.astimezone(IST):%H:%M} ({now.date()})", idx)


async def call2_check_job() -> None:
    """09:30: every variant must have a final Call 2 decision (ENTER/SKIP) by now."""
    if not (settings.intraday_hunter_v2_enabled and is_trading_day(now_ist().date())):
        return
    from app.services.intraday_hunter import store
    from app.services.intraday_hunter_v2.params import v2_active
    d = now_ist().date()
    v2_on = await v2_active()
    async with async_session_factory() as session:
        for variant, enabled in (("v1", settings.intraday_hunter_enabled), ("v2", v2_on)):
            if not enabled:
                continue
            run = await store.get_run(session, d, variant)
            if run is None or (run.decision or "").upper() not in ("ENTER", "SKIP"):
                state = "no run row" if run is None else f"status={run.status} decision={run.decision}"
                await alert("call2_missing", f"{variant} Call 2 has no decision by 09:30 on {d} "
                                             f"({state})", variant)


async def grade_job() -> None:
    """Grade today (also usable for a past date via the API)."""
    d = now_ist().date()
    if not await _enabled_today():
        return
    from app.services.intraday_hunter_v2 import grading
    try:
        async with async_session_factory() as session:
            await grading.grade_day(session, d)
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.exception("IH v2 grading failed for %s", d)
        await alert("grade", f"grading failed for {d}: {type(e).__name__}", "grade")


async def weekly_review_job() -> None:
    from app.services.intraday_hunter_v2.params import v2_active
    if not await v2_active():
        return
    from app.services.intraday_hunter_v2 import review
    try:
        async with async_session_factory() as session:
            row = await review.run_weekly_review(session, now_ist().date())
            await session.commit()
        from app.agent.notification import send_telegram
        await send_telegram(review.telegram_summary(row))
    except Exception:  # noqa: BLE001
        logger.exception("IH v2 weekly review failed")


async def start_intraday_hunter_v2_scheduler() -> None:
    """Register every v2 job and start the scheduler."""
    def cron(**kw):
        return CronTrigger(timezone=IST, **kw)

    add = scheduler.add_job
    for hh in (0, 6, 8):
        add(plan_job, cron(hour=hh, minute=0), args=[f"{hh:02d}:00"],
            id=f"ih_v2_plan_{hh:02d}00", replace_existing=True)
    add(plan_missing_job, cron(hour=8, minute=30), id="ih_v2_plan_missing_0830", replace_existing=True)
    add(call1_job, cron(hour=8, minute=45), id="ih_v2_call1_0845", replace_existing=True)
    add(orderflow_reset_job, cron(hour=9, minute=5), id="ih_v2_orderflow_reset_0905", replace_existing=True)
    add(capture_job, cron(hour=9, minute=10), args=["09:10"], id="ih_v2_capture_0910", replace_existing=True)
    add(capture_job, cron(hour=9, minute=16, second=30), args=["09:16 recentre"],
        id="ih_v2_capture_0916", replace_existing=True)
    add(candle_check_job, cron(hour="9-15", minute="20,50"), id="ih_v2_candle_check", replace_existing=True)
    add(call2_check_job, cron(hour=9, minute=30), id="ih_v2_call2_check_0930", replace_existing=True)
    add(live_job, cron(hour=13, minute=0), args=["13:00"], id="ih_v2_live_1300", replace_existing=True)
    add(live_job, cron(hour=15, minute=0), args=["15:00"], id="ih_v2_live_1500", replace_existing=True)
    add(grade_job, cron(hour=16, minute=0), id="ih_v2_grade_1600", replace_existing=True)
    add(weekly_review_job, cron(day_of_week="sat", hour=10, minute=0),
        id="ih_v2_weekly_review", replace_existing=True)
    scheduler.start()
    logger.info("Intraday Hunter v2 scheduler started: %s",
                ", ".join(j.id for j in scheduler.get_jobs()))


async def stop_intraday_hunter_v2_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Intraday Hunter v2 scheduler stopped")
