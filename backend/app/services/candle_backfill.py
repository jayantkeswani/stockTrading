"""Candle backfill — fetches historical 1m candles from Fyers and stores in DB.

Called on app startup to ensure strategies have data from the first candle:
1. Previous trading day — so strategies have PDH/PDL/PDC context
2. Today's elapsed candles — so a late start doesn't miss the trading window

Uses the Fyers SDK (FyersModel.history) which hits the correct
https://api-t1.fyers.in/data/history endpoint.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP, IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.models.market_data import MarketData1m

logger = logging.getLogger(__name__)

FYERS_TOKEN_KEY = "fyers:access_token"


def _resolve_fyers_symbol(symbol: str) -> str:
    """Resolve an internal symbol name to its Fyers-format symbol for history API.

    Index symbols are looked up from FYERS_SYMBOL_MAP.
    Stock symbols default to NSE:{SYMBOL}-EQ.
    """
    if symbol in FYERS_SYMBOL_MAP:
        return FYERS_SYMBOL_MAP[symbol]
    # Stock equity: NSE:TCS-EQ, NSE:RELIANCE-EQ etc.
    return f"NSE:{symbol}-EQ"


async def _get_all_backfill_symbols() -> dict[str, str]:
    """Get all symbols that need backfilling: FYERS_SYMBOL_MAP + strategy-configured symbols.

    Returns dict of {internal_symbol: fyers_symbol}.
    """
    from sqlalchemy import select
    from app.models.strategy_config import StrategyConfig

    # Start with default index symbols (skip INDIA VIX — no candle data)
    symbols = {
        k: v for k, v in FYERS_SYMBOL_MAP.items()
        if k != "INDIA VIX"
    }

    # Add symbols from all active strategy configs
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyConfig.symbols).where(
                    StrategyConfig.is_active == True  # noqa: E712
                )
            )
            rows = result.scalars().all()

        for symbol_list in rows:
            if not symbol_list:
                continue
            for sym in symbol_list:
                if sym not in symbols:
                    symbols[sym] = _resolve_fyers_symbol(sym)
    except Exception:
        logger.exception("Failed to load strategy symbols for backfill")

    return symbols


def _previous_trading_day(ref_date: date) -> date:
    """Return the most recent weekday before ref_date (skips weekends)."""
    d = ref_date - timedelta(days=1)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    return d


async def _has_candles_for_day(symbol: str, day: date) -> bool:
    """Check if DB already has candles for the given symbol and day."""
    day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)

    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count(MarketData1m.id)).where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp >= day_start,
                    MarketData1m.timestamp <= day_end,
                )
            )
        )
        count = result.scalar_one()
    return count > 0


def _fetch_history_via_sdk(token: str, fyers_symbol: str, day: date) -> list[dict]:
    """Fetch 1m candles using the Fyers SDK (synchronous).

    The SDK's history() method uses the correct DATA_API base URL
    (https://api-t1.fyers.in/data/history).
    """
    from fyers_apiv3.fyersModel import FyersModel

    fyers = FyersModel(client_id=settings.fyers_app_id, token=token)
    result = fyers.history({
        "symbol": fyers_symbol,
        "resolution": "1",
        "date_format": "1",
        "range_from": str(day),
        "range_to": str(day),
        "cont_flag": "1",
    })

    if result.get("s") != "ok":
        logger.error(
            "Fyers history error for %s: %s",
            fyers_symbol, result.get("message", result.get("s")),
        )
        return []

    candles = result.get("candles", [])
    return [
        {
            "timestamp": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5],
        }
        for c in candles
    ]


async def _backfill_symbol(token: str, symbol: str, fyers_symbol: str, day: date) -> int:
    """Fetch 1m candles for a single symbol/day and insert into DB.

    Returns the number of candles inserted.
    """
    import asyncio

    # SDK is synchronous — run in thread pool to avoid blocking the event loop
    candles = await asyncio.to_thread(
        _fetch_history_via_sdk, token, fyers_symbol, day,
    )

    if not candles:
        logger.warning("No historical candles returned for %s on %s", symbol, day)
        return 0

    # Filter to market hours only
    market_open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    market_close_dt = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)

    rows = []
    for c in candles:
        ts = datetime.fromtimestamp(c["timestamp"], tz=IST)
        if ts < market_open_dt or ts > market_close_dt:
            continue
        rows.append({
            "symbol": symbol,
            "timestamp": ts,
            "open": Decimal(str(c["open"])),
            "high": Decimal(str(c["high"])),
            "low": Decimal(str(c["low"])),
            "close": Decimal(str(c["close"])),
            "volume": int(c["volume"]),
        })

    if not rows:
        logger.warning("No market-hours candles for %s on %s", symbol, day)
        return 0

    # Upsert to avoid duplicates (on conflict do nothing)
    async with async_session_factory() as session:
        stmt = pg_insert(MarketData1m).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_market_data_symbol_time",
        )
        await session.execute(stmt)
        await session.commit()

    logger.info("Backfilled %d candles for %s on %s", len(rows), symbol, day)
    return len(rows)


async def backfill_previous_day():
    """Backfill previous trading day's 1m candles for all tracked symbols.

    Includes: FYERS_SYMBOL_MAP indices + symbols from active strategy configs.
    Skips symbols that already have data for that day.
    Called on app startup from main.py lifespan.
    """
    r = get_redis()
    token = await r.get(FYERS_TOKEN_KEY)
    if not token:
        logger.warning("No Fyers token — cannot backfill candles")
        return

    today = datetime.now(IST).date()
    prev_day = _previous_trading_day(today)

    symbols = await _get_all_backfill_symbols()
    logger.info(
        "Backfilling candles for previous trading day %s (%d symbols)",
        prev_day, len(symbols),
    )

    total = 0
    for symbol, fyers_symbol in symbols.items():
        if await _has_candles_for_day(symbol, prev_day):
            logger.info("Candles already exist for %s on %s — skipping", symbol, prev_day)
            continue

        try:
            count = await _backfill_symbol(token, symbol, fyers_symbol, prev_day)
            total += count
        except Exception:
            logger.exception("Failed to backfill %s", symbol)

    logger.info("Backfill complete: %d total candles inserted for %s", total, prev_day)


async def backfill_today():
    """Backfill today's elapsed 1m candles from market open until now.

    Handles the late-start scenario: if the backend starts at e.g. 9:40,
    this fetches 9:15–9:40 candles so the strategy has enough data to
    generate signals as soon as the trading window opens at 9:45.

    Skips if market hasn't opened yet or it's a weekend.
    """
    now = datetime.now(IST)
    today = now.date()

    # Skip weekends
    if today.weekday() >= 5:
        return

    market_open_dt = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    # Skip if market hasn't opened yet
    if now <= market_open_dt:
        logger.info("Market hasn't opened yet — skipping today's backfill")
        return

    r = get_redis()
    token = await r.get(FYERS_TOKEN_KEY)
    if not token:
        logger.warning("No Fyers token — cannot backfill today's candles")
        return

    symbols = await _get_all_backfill_symbols()
    logger.info(
        "Backfilling today's candles from %s to %s (%d symbols)",
        MARKET_OPEN, now.strftime("%H:%M"), len(symbols),
    )

    total = 0
    for symbol, fyers_symbol in symbols.items():
        try:
            count = await _backfill_symbol(token, symbol, fyers_symbol, today)
            total += count
        except Exception:
            logger.exception("Failed to backfill today's candles for %s", symbol)

    logger.info("Today's backfill complete: %d total candles inserted", total)
