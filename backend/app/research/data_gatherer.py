"""Pre-fetches shared data that multiple research agents need.

Runs BEFORE sub-agents launch. Results passed via ResearchContext.
This avoids N agents all fetching the same price history / stock info.

If fundamental data is not in DB (stock not configured for CAN SLIM),
fetches it on-demand and stores it for future use.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IST
from app.data_sources import yfinance_client
from app.data_feed.symbol_master import symbol_master
from app.models.fundamental_data import StockFundamental
from app.research.agents.base import ResearchContext

logger = logging.getLogger(__name__)

# Consider data stale if older than 12 hours
STALE_THRESHOLD_HOURS = 12


async def gather_context(symbol: str, db: AsyncSession) -> ResearchContext:
    """Pre-fetch shared data for all research agents.

    1. Validate symbol via symbol master
    2. Fetch stock info (yfinance) — has current price, market cap, 52w range
    3. Fetch 1-year daily price history (yfinance)
    4. Check stock_fundamentals table — if missing or stale, fetch and store on-demand
    5. Determine F&O eligibility

    All fetched concurrently where possible.
    """
    # Resolve display name and Fyers symbol
    display_name = symbol
    fyers_symbol = None
    if symbol_master.is_loaded:
        results = symbol_master.search(symbol, limit=1)
        if results:
            # Dict keys: s=fyers_symbol, d=display_name, n=short_name, g=segment
            display_name = results[0].get("d", symbol)
            fyers_symbol = results[0].get("s")  # e.g. "NSE:TCS-EQ"

    # Concurrent fetches
    stock_info_task = yfinance_client.get_stock_info(symbol)
    price_history_task = yfinance_client.get_price_history(symbol, period="1y")
    fundamental_task = _get_existing_fundamental(symbol, db)

    stock_info, price_history, existing_fundamental = await asyncio.gather(
        stock_info_task, price_history_task, fundamental_task
    )

    # If fundamental data is missing or stale, fetch on-demand and store
    if _is_stale(existing_fundamental):
        logger.info(
            "Fundamental data %s for %s — fetching on-demand",
            "missing" if not existing_fundamental else "stale",
            symbol,
        )
        existing_fundamental = await _fetch_and_store_fundamental(symbol, db)

    # Determine F&O eligibility
    is_fo = False
    if existing_fundamental and existing_fundamental.is_fo_eligible:
        is_fo = True

    current_price = None
    market_cap_cr = None
    if stock_info:
        current_price = stock_info.current_price
        market_cap_cr = stock_info.market_cap_cr

    return ResearchContext(
        symbol=symbol,
        display_name=display_name,
        stock_info=stock_info,
        price_history_1y=price_history,
        current_price=current_price,
        market_cap_cr=market_cap_cr,
        is_fo_eligible=is_fo,
        existing_fundamental=existing_fundamental,
        fyers_symbol=fyers_symbol,
    )


def _is_stale(fundamental: StockFundamental | None) -> bool:
    """Check if fundamental data is missing or older than threshold."""
    if fundamental is None:
        return True
    if fundamental.last_refreshed_at is None:
        return True
    now = datetime.now(IST)
    age = now - fundamental.last_refreshed_at
    return age > timedelta(hours=STALE_THRESHOLD_HOURS)


async def _get_existing_fundamental(
    symbol: str, db: AsyncSession
) -> StockFundamental | None:
    """Check if we have pre-computed CAN SLIM data for this symbol."""
    try:
        result = await db.execute(
            select(StockFundamental).where(StockFundamental.symbol == symbol)
        )
        return result.scalar_one_or_none()
    except Exception:
        logger.debug("No existing fundamental data for %s", symbol)
        return None


async def _fetch_and_store_fundamental(
    symbol: str, db: AsyncSession
) -> StockFundamental | None:
    """Fetch fundamental data on-demand and store it in the DB.

    Reuses the same logic as the periodic fundamental_data_task
    but for a single symbol, triggered by research demand.
    """
    try:
        from app.tasks.fundamental_data_task import _fetch_and_store_symbol

        await _fetch_and_store_symbol(symbol, lot_sizes=None)

        # Read back the freshly stored data
        result = await db.execute(
            select(StockFundamental).where(StockFundamental.symbol == symbol)
        )
        fundamental = result.scalar_one_or_none()

        if fundamental:
            logger.info("On-demand fundamental data stored for %s (score=%.1f)",
                        symbol, float(fundamental.canslim_score or 0))
        return fundamental

    except Exception:
        logger.warning("On-demand fundamental fetch failed for %s", symbol, exc_info=True)
        return None
