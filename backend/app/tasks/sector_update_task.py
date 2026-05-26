"""Sector classification auto-update task.

Runs daily at 7:00 AM IST. Fetches sector/industry from yfinance for
F&O stocks missing sector classification in stock_fundamentals.

Data flow:
    scheduler -> update_sectors()
        -> find symbols with sector IS NULL in stock_fundamentals
        -> find S5 permanent watchlist symbols not in stock_fundamentals
        -> yfinance Ticker.info -> sector, industry
        -> upsert into stock_fundamentals
        -> reload sectors.py DB cache
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def update_sectors() -> int:
    """Fetch sector/industry for stocks missing classification."""
    from app.core.database import async_session_factory
    from app.models.fundamental_data import StockFundamental
    from app.data_sources import yfinance_client
    from sqlalchemy import select, or_
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with async_session_factory() as session:
        result = await session.execute(
            select(StockFundamental.symbol).where(
                or_(
                    StockFundamental.sector.is_(None),
                    StockFundamental.sector == "",
                )
            )
        )
        missing = list(result.scalars().all())

    from app.core.redis import get_redis
    import json
    r = get_redis()
    perm_raw = await r.get("strat5:watchlist:permanent")
    perm_symbols = set(json.loads(perm_raw)) if perm_raw else set()

    async with async_session_factory() as session:
        result = await session.execute(
            select(StockFundamental.symbol)
        )
        all_in_db = set(result.scalars().all())

    new_symbols = [s for s in perm_symbols if s not in all_in_db]
    targets = list(set(missing + new_symbols))

    if not targets:
        logger.info("All stocks have sector classification — nothing to update")
        return 0

    logger.info(
        "Updating sector for %d symbols (%d missing, %d new)",
        len(targets), len(missing), len(new_symbols),
    )

    updated = 0
    for symbol in targets:
        try:
            info = await yfinance_client.get_stock_info(symbol)
            if info and info.sector:
                async with async_session_factory() as session:
                    stmt = pg_insert(StockFundamental).values(
                        symbol=symbol,
                        yfinance_ticker=f"{symbol}.NS",
                        sector=info.sector,
                        industry=info.industry,
                        is_fo_eligible=True,
                        last_refreshed_at=datetime.now(IST),
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["symbol"],
                        set_={"sector": info.sector, "industry": info.industry},
                    )
                    await session.execute(stmt)
                    await session.commit()
                updated += 1
                logger.debug("Updated sector for %s: %s / %s", symbol, info.sector, info.industry)
        except Exception:
            logger.warning("Failed to fetch sector for %s", symbol, exc_info=True)

        if symbol != targets[-1]:
            await asyncio.sleep(1.0)

    from app.data.sectors import load_db_sectors
    await load_db_sectors()

    logger.info("Sector update complete: %d/%d symbols updated", updated, len(targets))
    return updated


async def start_sector_update_scheduler():
    """Start the sector update scheduler (07:00 IST daily)."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        update_sectors,
        trigger=CronTrigger(hour=7, minute=0, timezone=IST),
        id="sector_update",
        name="Update sector classification (07:00 IST)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Sector update scheduler started (07:00 IST)")


async def stop_sector_update_scheduler():
    """Stop the sector update scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Sector update scheduler stopped")
