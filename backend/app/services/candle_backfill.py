"""Candle backfill — fetches historical 1m candles from Fyers and stores in DB.

Called on app startup to ensure strategies have data from the first candle:
1. Previous trading day — so strategies have PDH/PDL/PDC context
2. Today's elapsed candles — so a late start doesn't miss the trading window

Uses the Fyers SDK (FyersModel.history) which hits the correct
https://api-t1.fyers.in/data/history endpoint.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP, IST, MARKET_CLOSE, MARKET_OPEN, NSE_HOLIDAYS
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.models.market_data import MarketData1m

logger = logging.getLogger(__name__)

FYERS_TOKEN_KEY = "fyers:access_token"


def _resolve_fyers_symbol(symbol: str) -> str:
    """Resolve an internal symbol name to its Fyers-format symbol for history API.

    Index symbols are looked up from FYERS_SYMBOL_MAP.
    Stock symbols default to NSE:{SYMBOL}-EQ.

    NOTE: This is a last-resort fallback. The primary path uses the symbol_map
    stored on strategy_configs (populated at symbol insertion time from the
    symbol master search results). This function is only called for symbols
    that are missing from symbol_map (e.g. legacy data before symbol_map existed).
    """
    if symbol in FYERS_SYMBOL_MAP:
        return FYERS_SYMBOL_MAP[symbol]
    return f"NSE:{symbol}-EQ"


async def _get_all_backfill_symbols() -> dict[str, str]:
    """Get all symbols that need backfilling: FYERS_SYMBOL_MAP + strategy-configured + index futures.

    Returns dict of {internal_symbol: fyers_symbol}.
    Uses symbol_map from strategy_configs for accurate Fyers symbols (stored at insertion time).
    Index futures (e.g. NIFTY_FUT → NSE:NIFTY25MAYFUT) are included so that
    strategy_runner's futures-volume buffers are populated on mid-day restart.
    """
    from sqlalchemy import select
    from app.models.strategy_config import StrategyConfig

    # Start with default index symbols (skip INDIA VIX — no candle data)
    symbols = {
        k: v for k, v in FYERS_SYMBOL_MAP.items()
        if k != "INDIA VIX"
    }

    # Add symbols from all active strategy configs, using stored symbol_map
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyConfig.symbols, StrategyConfig.symbol_map).where(
                    StrategyConfig.is_active == True  # noqa: E712
                )
            )
            rows = result.all()

        for symbol_list, symbol_map in rows:
            if not symbol_list:
                continue
            sym_map = symbol_map or {}
            for sym in symbol_list:
                if sym not in symbols:
                    # Use stored Fyers symbol if available, fall back to reconstruction
                    symbols[sym] = sym_map.get(sym) or _resolve_fyers_symbol(sym)
    except Exception:
        logger.exception("Failed to load strategy symbols for backfill")

    # Strategy 5 dynamic watchlist — populated by the morning screener at 8:30 AM.
    # These symbols are not in strategy_configs.symbols so they'd be missed on reconnect.
    try:
        from datetime import datetime as _dt
        today_str = _dt.now(IST).strftime("%Y-%m-%d")
        r = get_redis()
        raw = await r.get(f"strat5:watchlist:{today_str}")
        if raw:
            import json as _json
            for item in _json.loads(raw):
                sym = item.get("symbol")
                if sym and sym not in symbols:
                    symbols[sym] = _resolve_fyers_symbol(sym)
    except Exception:
        logger.exception("Failed to load S5 watchlist symbols for backfill")

    # Strategy 5 permanent watchlist — user-pinned stocks that may not appear
    # in the dynamic screener output until 8:30 AM.  Backfilling them here
    # ensures PDH/PDL/PDC context is available from the first candle.
    try:
        perm_raw = await r.get("strat5:watchlist:permanent")
        if perm_raw:
            import json as _json2
            for sym in _json2.loads(perm_raw):
                if sym and sym not in symbols:
                    symbols[sym] = _resolve_fyers_symbol(sym)
    except Exception:
        logger.exception("Failed to load permanent watchlist symbols for backfill")

    # Near-month index futures for VWAP volume sourcing.
    # Internal key "{INDEX}_FUT" matches strategy_runner._init_index_futures().
    try:
        from app.core.constants import INDEX_SYMBOLS
        from app.services.futures_resolver import resolve_index_futures_symbol

        for index in INDEX_SYMBOLS:
            result = await resolve_index_futures_symbol(index)
            if result is not None:
                fyers_sym, _expiry = result
                internal_name = f"{index}_FUT"
                symbols[internal_name] = fyers_sym
                logger.info("Including index futures in backfill: %s → %s", internal_name, fyers_sym)
    except Exception:
        logger.exception("Failed to resolve index futures for backfill")

    return symbols


def _previous_trading_day(ref_date: date) -> date:
    """Return the most recent NSE trading day before ref_date (skips weekends and holidays)."""
    d = ref_date - timedelta(days=1)
    while d.weekday() >= 5 or d in NSE_HOLIDAYS:
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


async def _latest_candle_time(symbol: str, day: date) -> datetime | None:
    """Return the latest candle timestamp for the given symbol and day, or None."""
    day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)

    async with async_session_factory() as session:
        result = await session.execute(
            select(func.max(MarketData1m.timestamp)).where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp >= day_start,
                    MarketData1m.timestamp <= day_end,
                )
            )
        )
        return result.scalar_one()


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
            "Fyers history error for %s: %s (full: %s)",
            fyers_symbol,
            result.get("message") or result.get("s") or "unknown",
            {k: v for k, v in result.items() if k != "candles"},
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
    fetched = 0
    for symbol, fyers_symbol in symbols.items():
        if await _has_candles_for_day(symbol, prev_day):
            logger.info("Candles already exist for %s on %s — skipping", symbol, prev_day)
            continue

        try:
            count = await _backfill_symbol(token, symbol, fyers_symbol, prev_day)
            total += count
            fetched += 1
            if fetched % 5 == 0:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.3)
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

    # Skip weekends and NSE holidays
    from app.core.utils import is_trading_day
    if not is_trading_day(today):
        logger.info("Not a trading day (%s) — skipping today's backfill", today)
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
    skipped = 0
    fetched = 0
    freshness_threshold = timedelta(minutes=2)
    for symbol, fyers_symbol in symbols.items():
        try:
            latest = await _latest_candle_time(symbol, today)
            if latest is not None and (now - latest) < freshness_threshold:
                skipped += 1
                continue
            count = await _backfill_symbol(token, symbol, fyers_symbol, today)
            total += count
            fetched += 1
            if fetched % 5 == 0:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.3)
        except Exception:
            logger.exception("Failed to backfill today's candles for %s", symbol)

    if skipped:
        logger.info("Today's backfill: skipped %d symbols (candles within %ds)", skipped, int(freshness_threshold.total_seconds()))
    logger.info("Today's backfill complete: %d total candles inserted", total)


async def backfill_deep_history(days: int = 120) -> None:
    """Backfill extended history for CAN SLIM symbols that need daily pattern detection.

    Fetches ~120 calendar days of 1m candles in weekly chunks (to stay within
    Fyers API limits). Only runs for symbols that have fewer than 50 trading days
    of data — once history is populated, the daily backfill keeps it current.

    Called on startup as a background task. Non-blocking.
    """
    from app.models.strategy_config import StrategyConfig

    r = get_redis()
    token = await r.get(FYERS_TOKEN_KEY)
    if not token:
        logger.warning("No Fyers token — cannot run deep backfill")
        return

    # Find CAN SLIM symbols that need history
    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig.symbols, StrategyConfig.symbol_map).where(
                StrategyConfig.strategy_name == "can_slim"
            )
        )
        row = result.one_or_none()

    if not row or not row[0]:
        return

    symbols_list, sym_map = row
    sym_map = sym_map or {}

    today = datetime.now(IST).date()
    start_date = today - timedelta(days=days)

    symbols_needing_backfill = []
    for sym in symbols_list:
        # Check how many trading days of data we have
        async with async_session_factory() as session:
            result = await session.execute(
                select(func.count(func.distinct(func.date(MarketData1m.timestamp)))).where(
                    MarketData1m.symbol == sym,
                )
            )
            day_count = result.scalar_one()

        if day_count < 50:
            fyers_sym = sym_map.get(sym, f"NSE:{sym}-EQ")
            symbols_needing_backfill.append((sym, fyers_sym))

    if not symbols_needing_backfill:
        logger.info("Deep backfill: all CAN SLIM symbols have sufficient history")
        return

    logger.info(
        "Deep backfill: %d symbols need history (%d days from %s)",
        len(symbols_needing_backfill), days, start_date,
    )

    for sym, fyers_sym in symbols_needing_backfill:
        total_candles = 0
        # Fetch in 7-day chunks to avoid API limits
        chunk_start = start_date
        while chunk_start < today:
            chunk_end = min(chunk_start + timedelta(days=6), today - timedelta(days=1))
            try:
                candles = await asyncio.to_thread(
                    _fetch_history_range_via_sdk, token, fyers_sym, chunk_start, chunk_end,
                )
                if candles:
                    count = await _persist_candles(sym, candles)
                    total_candles += count
            except Exception:
                logger.debug("Deep backfill chunk failed for %s (%s to %s)", sym, chunk_start, chunk_end)

            chunk_start = chunk_end + timedelta(days=1)
            await asyncio.sleep(0.5)  # Rate limit between chunks

        logger.info("Deep backfill: %s — %d candles inserted", sym, total_candles)

    logger.info("Deep backfill complete")


def _fetch_history_range_via_sdk(
    token: str, fyers_symbol: str, from_date: date, to_date: date
) -> list[dict]:
    """Fetch 1m candles for a date range using the Fyers SDK."""
    from fyers_apiv3.fyersModel import FyersModel

    fyers = FyersModel(client_id=settings.fyers_app_id, token=token)
    result = fyers.history({
        "symbol": fyers_symbol,
        "resolution": "1",
        "date_format": "1",
        "range_from": str(from_date),
        "range_to": str(to_date),
        "cont_flag": "1",
    })

    if result.get("s") != "ok":
        return []

    return [
        {
            "timestamp": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5],
        }
        for c in result.get("candles", [])
    ]


async def _persist_candles(symbol: str, candles: list[dict]) -> int:
    """Persist a list of candle dicts to MarketData1m. Returns count inserted."""
    rows = []
    for c in candles:
        ts = datetime.fromtimestamp(c["timestamp"], tz=IST)
        # Filter to market hours
        if ts.time() < MARKET_OPEN or ts.time() > MARKET_CLOSE:
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
        return 0

    async with async_session_factory() as session:
        stmt = pg_insert(MarketData1m).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_market_data_symbol_time")
        await session.execute(stmt)
        await session.commit()

    return len(rows)
