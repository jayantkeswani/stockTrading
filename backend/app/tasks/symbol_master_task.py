"""Scheduled task for daily symbol master refresh.

Runs daily at 8:00 AM IST (before the login task at 8:55 AM)
to ensure fresh symbol data is available for the trading day.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=IST)


async def _refresh_symbol_master() -> None:
    """Download and refresh the symbol master data."""
    from app.data_feed.symbol_master import symbol_master

    logger.info("Running scheduled symbol master refresh...")
    await symbol_master.refresh()
    logger.info("Symbol master refresh complete (%d symbols)", symbol_master.count)


async def start_symbol_master_scheduler() -> None:
    """Start the daily symbol master refresh scheduler."""
    scheduler.add_job(
        _refresh_symbol_master,
        trigger=CronTrigger(hour=8, minute=0, timezone=IST),
        id="symbol_master_daily",
        name="Symbol Master Daily Refresh",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Symbol master scheduler started (daily at 8:00 AM IST)")


async def stop_symbol_master_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Symbol master scheduler stopped")
