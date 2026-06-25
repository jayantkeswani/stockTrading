"""Intraday Hunter — Call 1 (pre-open thesis) scheduler.

08:45 AM IST daily: run the pre-open thesis for the day onto the `intraday_hunter_runs` row.
The Call 2 decision is driven separately by the candle-close watcher
(`services/intraday_hunter/watcher.py`), so there is no cron for Call 2. Only runs on
trading days, and only when the agent is enabled. Re-running Call 1 overwrites the thesis.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=IST)


async def _run_call1() -> None:
    """Generate the day's pre-open thesis (Call 1) at 08:45 IST."""
    if not settings.intraday_hunter_enabled:
        logger.debug("Intraday Hunter disabled — skipping Call 1")
        return
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping Intraday Hunter Call 1")
        return

    from app.services.intraday_hunter import thesis

    try:
        async with async_session_factory() as session:
            run = await thesis.run_call1(
                session, today, variant=settings.intraday_hunter_variant
            )
            await session.commit()
        logger.info(
            "Intraday Hunter Call 1 for %s — status=%s", today, run.status
        )
    except Exception:
        logger.exception("Intraday Hunter Call 1 failed")


async def start_intraday_hunter_scheduler() -> None:
    """Register the 08:45 IST Call 1 job and start the scheduler."""
    scheduler.add_job(
        _run_call1,
        CronTrigger(hour=8, minute=45, timezone=IST),
        id="intraday_hunter_call1",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Intraday Hunter scheduler started (Call 1 thesis 08:45 IST)")


async def stop_intraday_hunter_scheduler() -> None:
    """Shut down the Intraday Hunter scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Intraday Hunter scheduler stopped")
