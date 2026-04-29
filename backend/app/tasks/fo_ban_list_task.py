"""NSE F&O ban list task — fetches and caches the daily MWPL ban list.

Runs daily at 7:00 AM IST (before the morning screener at 8:30 AM) so the
ban list is always warm in Redis before screener and signal generation begin.
Also runs once on startup via gap-fill to ensure today's list is cached.

The ban list is used by:
  - morning_screener._stage1_quantitative() — filters banned symbols before scoring
  - strategy_runner._check_strategy_risk_limits() — blocks signals for banned symbols

Redis key: ``nse:fo_ban_list:{YYYY-MM-DD}``  TTL: 24 hours
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scheduled_fetch() -> None:
    """Scheduled wrapper — skips non-trading days."""
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping F&O ban list fetch")
        return

    from app.data_sources.nse_client import get_fo_ban_list

    try:
        ban_set = await get_fo_ban_list(today)
        logger.info(
            "F&O ban list fetched for %s: %d symbols banned",
            today, len(ban_set),
        )
    except Exception:
        logger.exception("F&O ban list scheduled fetch failed")


async def _fetch_on_startup() -> None:
    """Fetch today's F&O ban list on startup if not already cached."""
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping F&O ban list startup fetch")
        return

    from app.core.redis import get_redis
    from app.data_sources.nse_client import get_fo_ban_list

    r = get_redis()
    cache_key = f"nse:fo_ban_list:{today}"
    cached = await r.get(cache_key)
    if cached:
        import json
        ban_set = set(json.loads(cached))
        logger.info(
            "F&O ban list for %s already in Redis (%d symbols) — skipping startup fetch",
            today, len(ban_set),
        )
        return

    try:
        ban_set = await get_fo_ban_list(today)
        logger.info(
            "F&O ban list startup fetch for %s: %d symbols banned",
            today, len(ban_set),
        )
    except Exception:
        logger.exception("F&O ban list startup fetch failed")


async def start_fo_ban_list_scheduler() -> None:
    """Start the daily F&O ban list scheduler (7:00 AM IST) and seed today's list."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        _scheduled_fetch,
        CronTrigger(hour=7, minute=0, timezone=IST),
        id="nse_fo_ban_list_fetch",
        name="Fetch NSE F&O ban list",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("NSE F&O ban list scheduler started (daily 7:00 AM IST)")

    # Seed today's ban list on startup (non-blocking background task)
    import asyncio

    from app.core.task_registry import task_registry

    task = asyncio.create_task(_fetch_on_startup(), name="fo_ban_list_startup_fetch")
    task_registry.track_asyncio_task(
        "fo_ban_list_startup_fetch", task,
        metadata={"description": "Seed today's F&O ban list on startup"},
    )


async def stop_fo_ban_list_scheduler() -> None:
    """Stop the F&O ban list scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("NSE F&O ban list scheduler stopped")
