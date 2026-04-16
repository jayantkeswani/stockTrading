"""OI snapshot task — periodically fetches option chain data from Fyers.

Runs every 3 minutes during market hours. Fetches the option chain for each
index symbol and persists strike-level OI data to the oi_snapshots table.

The strategy_runner reads from oi_snapshots to build OIAnalysis for
MarketContext, providing OI confirmation for trade signals.
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP, IST, MARKET_CLOSE, MARKET_OPEN
from app.core.utils import is_market_open

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Symbols to fetch OI for — only indices (not VIX, not stocks)
OI_SYMBOLS = {
    k: v for k, v in FYERS_SYMBOL_MAP.items()
    if k != "INDIA VIX"
}

OI_FETCH_INTERVAL_MINUTES = 3


async def fetch_oi_snapshots():
    """Fetch option chain OI data for all index symbols and persist to DB.

    Skips if market is closed (OI doesn't change after hours).
    """
    if not is_market_open():
        return

    from app.core.redis import get_redis

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot fetch OI data")
        return

    total_rows = 0
    for symbol in OI_SYMBOLS:
        try:
            count = await _fetch_and_persist_oi(token, symbol)
            total_rows += count
        except Exception:
            logger.exception("Failed to fetch OI for %s", symbol)

    if total_rows > 0:
        logger.info("OI snapshot: persisted %d rows across %d symbols", total_rows, len(OI_SYMBOLS))


async def _fetch_and_persist_oi(token: str, symbol: str) -> int:
    """Fetch option chain for a single symbol and insert into oi_snapshots.

    Returns the number of rows inserted.
    """
    from app.data_feed.fyers_client import FyersClient

    client = FyersClient(access_token=token)
    try:
        data = await client.get_option_chain(symbol)
    finally:
        await client.close()

    if not data or data.get("s") != "ok":
        logger.warning(
            "Fyers option chain error for %s: %s",
            symbol, data.get("message", "unknown error") if data else "no response",
        )
        return 0

    return await _parse_and_store(symbol, data)


async def _parse_and_store(symbol: str, data: dict) -> int:
    """Parse Fyers option chain response and insert OI snapshots.

    Fyers v3 option chain response structure:
    {
        "s": "ok",
        "data": {
            "expiryData": [
                {
                    "date": "2026-04-22",
                    "expiry": 1745330999,
                    ...
                }
            ],
            "optionsChain": [
                {
                    "strike_price": 24000,
                    "call_options": {
                        "ltp": 250.0,
                        "oi": 1234567,
                        "volume": 98765,
                        "chng_oi": 5000,
                        ...
                    },
                    "put_options": {
                        "ltp": 180.0,
                        "oi": 2345678,
                        "volume": 87654,
                        "chng_oi": -3000,
                        ...
                    }
                },
                ...
            ]
        }
    }
    """
    from decimal import Decimal
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session_factory
    from app.models.oi_snapshot import OISnapshot

    now = datetime.now(IST)
    # Round to nearest minute for consistent timestamps
    now = now.replace(second=0, microsecond=0)

    chain_data = data.get("data", {})
    options_chain = chain_data.get("optionsChain", [])

    if not options_chain:
        logger.debug("Empty option chain for %s", symbol)
        return 0

    # Get expiry date from the response
    expiry_data = chain_data.get("expiryData", [])
    expiry_date = None
    if expiry_data:
        expiry_str = expiry_data[0].get("date", "")
        if expiry_str:
            try:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except ValueError:
                pass

    if expiry_date is None:
        logger.warning("Could not determine expiry date for %s OI snapshot", symbol)
        return 0

    rows = []
    for strike_data in options_chain:
        strike_price = strike_data.get("strike_price", 0)
        if strike_price <= 0:
            continue

        call = strike_data.get("call_options", {})
        put = strike_data.get("put_options", {})

        if call and call.get("oi", 0) > 0:
            rows.append({
                "symbol": symbol,
                "expiry_date": expiry_date,
                "strike_price": Decimal(str(strike_price)),
                "option_type": "CE",
                "open_interest": int(call.get("oi", 0)),
                "oi_change": int(call.get("chng_oi", 0)),
                "volume": int(call.get("volume", 0)),
                "timestamp": now,
            })

        if put and put.get("oi", 0) > 0:
            rows.append({
                "symbol": symbol,
                "expiry_date": expiry_date,
                "strike_price": Decimal(str(strike_price)),
                "option_type": "PE",
                "open_interest": int(put.get("oi", 0)),
                "oi_change": int(put.get("chng_oi", 0)),
                "volume": int(put.get("volume", 0)),
                "timestamp": now,
            })

    if not rows:
        return 0

    async with async_session_factory() as session:
        stmt = pg_insert(OISnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_oi_snapshot")
        await session.execute(stmt)
        await session.commit()

    return len(rows)


async def start_oi_snapshot_scheduler():
    """Start the periodic OI snapshot scheduler."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        fetch_oi_snapshots,
        trigger=IntervalTrigger(minutes=OI_FETCH_INTERVAL_MINUTES, timezone=IST),
        id="oi_snapshot_fetch",
        name="Fetch OI snapshots",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("OI snapshot scheduler started (every %d minutes)", OI_FETCH_INTERVAL_MINUTES)


async def stop_oi_snapshot_scheduler():
    """Stop the OI snapshot scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("OI snapshot scheduler stopped")
