"""On-demand historical option candle fetcher for accurate backtest mode.

Data sources (in priority order):
1. In-memory cache (populated from DB after first lookup).
2. market_data_1m table — option candles collected live via WebSocket feed when
   the system was trading that contract. These are the primary data source.
3. Fyers historical API — confirmed NOT available on the free plan (returns
   no_data even for active contracts). Kept as fallback for paid-plan users.

Cache design:
  Cache key: "{fyers_option_symbol}_{trade_date}" — always loads the FULL
  trading day to avoid the bug where a narrow 5-min entry query populates the
  cache, and a later full-day exit simulation query hits the stale narrow cache.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
from app.indicators.candle_patterns import Candle

logger = logging.getLogger(__name__)

# In-memory cache: "{fyers_option_symbol}_{YYYY-MM-DD}" -> full-day candle list
_candle_cache: dict[str, list[tuple[datetime, Candle]]] = {}

CHUNK_DAYS = 6
RATE_LIMIT_SLEEP = 0.5


async def ensure_option_candles(
    fyers_option_symbol: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[datetime, Candle]]:
    """Return 1m candles for an option symbol between start_ts and end_ts.

    Always loads the full trading day into cache to prevent stale-narrow-cache
    issues when entry (5 min) and exit (full day) queries share the same symbol.
    Returns list of (timestamp, Candle) sorted ascending.
    """
    trade_date = start_ts.date()
    cache_key = f"{fyers_option_symbol}_{trade_date}"

    if cache_key not in _candle_cache:
        # Fetch the complete trading day from DB, not just the requested window
        day_start = datetime.combine(trade_date, MARKET_OPEN, tzinfo=IST)
        day_end = datetime.combine(trade_date, MARKET_CLOSE, tzinfo=IST)

        full_day = await _fetch_from_db(fyers_option_symbol, day_start, day_end)
        if not full_day:
            # Fallback: try Fyers API (works only on paid plans)
            full_day = await _fetch_from_fyers(fyers_option_symbol, trade_date, trade_date)
        _candle_cache[cache_key] = full_day

    return _filter_range(_candle_cache[cache_key], start_ts, end_ts)


def clear_cache() -> None:
    """Clear the in-memory full-day candle cache (call between backtest runs)."""
    _candle_cache.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_from_db(
    symbol: str,
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[datetime, Candle]]:
    from sqlalchemy import and_, select
    from app.models.market_data import MarketData1m

    async with async_session_factory() as session:
        result = await session.execute(
            select(
                MarketData1m.timestamp,
                MarketData1m.open,
                MarketData1m.high,
                MarketData1m.low,
                MarketData1m.close,
                MarketData1m.volume,
            )
            .where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp >= start_ts,
                    MarketData1m.timestamp <= end_ts,
                )
            )
            .order_by(MarketData1m.timestamp)
        )
        rows = result.all()

    return [
        (
            r.timestamp,
            Candle(
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=int(r.volume or 0),
            ),
        )
        for r in rows
    ]


async def _fetch_from_fyers(
    fyers_option_symbol: str,
    start_date: date,
    end_date: date,
) -> list[tuple[datetime, Candle]]:
    """Fetch from Fyers SDK and persist to DB. Returns all fetched candles."""
    from app.core.redis import get_redis
    from app.services.candle_backfill import _fetch_history_range_via_sdk, _persist_candles

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot fetch option candles for %s", fyers_option_symbol)
        return []

    all_raw: list[dict] = []
    chunk_start = start_date

    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end_date)
        try:
            raw = await asyncio.to_thread(
                _fetch_history_range_via_sdk, token, fyers_option_symbol, chunk_start, chunk_end
            )
            if raw:
                all_raw.extend(raw)
                logger.debug(
                    "Option candles %s (%s→%s): %d rows",
                    fyers_option_symbol, chunk_start, chunk_end, len(raw),
                )
        except Exception:
            logger.exception("Failed to fetch option candles %s (%s→%s)", fyers_option_symbol, chunk_start, chunk_end)

        chunk_start = chunk_end + timedelta(days=1)
        await asyncio.sleep(RATE_LIMIT_SLEEP)

    if all_raw:
        await _persist_candles(fyers_option_symbol, all_raw)

    # Build typed list from raw dicts
    result: list[tuple[datetime, Candle]] = []
    for c in all_raw:
        ts = datetime.fromtimestamp(c["timestamp"], tz=IST)
        if ts.time() < MARKET_OPEN or ts.time() > MARKET_CLOSE:
            continue
        result.append((
            ts,
            Candle(
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=int(c["volume"]),
            ),
        ))

    result.sort(key=lambda x: x[0])
    return result


def _filter_range(
    candles: list[tuple[datetime, Candle]],
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[datetime, Candle]]:
    return [(ts, c) for ts, c in candles if start_ts <= ts <= end_ts]
