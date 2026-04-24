"""Futures resolver — translates stock-level signals into tradeable futures contracts.

Analogous to option_resolver.py but for stock futures. Given a stock symbol,
this module:
1. Finds the nearest monthly expiry (last Thursday of month for NSE stock futures)
2. Looks up the Fyers futures symbol via the symbol master
3. Fetches the futures LTP from Redis cache or Fyers REST
4. Gets the lot size from the symbol master
5. Estimates margin requirement (~18% of contract value)

Called by strategy_runner as post-processing for signals with
instrument_type == FUTURE.
"""

import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.constants import FUTURES_MARGIN_PCT, INDEX_FUTURES_EXPIRY_DOW, STOCK_FUTURES_EXPIRY_DOW
from app.core.utils import now_ist

logger = logging.getLogger(__name__)


@dataclass
class FuturesResolution:
    """Result of resolving a stock signal to a specific futures contract."""

    fyers_symbol: str  # e.g. "NSE:TCS26APRFUT"
    expiry_date: date
    lot_size: int
    ltp: float  # Last traded price of the futures contract
    margin_required: float  # Estimated SPAN + exposure margin


async def resolve_futures_contract(
    symbol: str,
    entry_price: float,
    from_date: date | None = None,
) -> FuturesResolution | None:
    """Resolve a stock symbol to its nearest-month futures contract.

    Args:
        symbol: Stock symbol, e.g. "TCS"
        entry_price: Expected entry price (used for margin estimation)
        from_date: Starting date for expiry search (defaults to today).
            Pass ``current_expiry + timedelta(days=1)`` to get the *next*
            month's contract when rolling an expiring position.

    Returns FuturesResolution or None if resolution fails.
    """
    today = from_date or now_ist().date()

    # 1. Find nearest monthly expiry
    expiry = _find_nearest_monthly_expiry(today)

    # 2. Search symbol master for the futures contract
    fyers_symbol = await _find_futures_symbol(symbol, expiry)
    if fyers_symbol is None:
        # Try next month's expiry
        next_month_expiry = _find_nearest_monthly_expiry(expiry + timedelta(days=7))
        fyers_symbol = await _find_futures_symbol(symbol, next_month_expiry)
        if fyers_symbol is None:
            logger.warning(
                "Could not find futures symbol for %s (tried expiry %s and %s)",
                symbol, expiry, next_month_expiry,
            )
            return None
        expiry = next_month_expiry

    # 3. Fetch LTP
    ltp = await _fetch_futures_ltp(fyers_symbol)
    if ltp is None or ltp <= 0:
        logger.warning("Could not fetch LTP for %s", fyers_symbol)
        # Fall back to entry_price estimate
        ltp = entry_price

    # 4. Get lot size from symbol master
    lot_size = await _get_lot_size(symbol, fyers_symbol)

    # 5. Estimate margin
    contract_value = ltp * lot_size
    margin = contract_value * FUTURES_MARGIN_PCT

    logger.info(
        "Futures resolved: %s → %s expiry=%s ltp=%.2f lot=%d margin=%.0f",
        symbol, fyers_symbol, expiry, ltp, lot_size, margin,
    )

    return FuturesResolution(
        fyers_symbol=fyers_symbol,
        expiry_date=expiry,
        lot_size=lot_size,
        ltp=ltp,
        margin_required=margin,
    )


def _find_nearest_monthly_expiry(from_date: date) -> date:
    """Find the last Thursday of the current month (NSE stock futures expiry).

    If today is past this month's last Thursday, returns next month's.
    """
    year, month = from_date.year, from_date.month

    expiry = _last_dow_of_month(year, month, STOCK_FUTURES_EXPIRY_DOW)

    # If past this month's expiry, use next month
    if from_date > expiry:
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        expiry = _last_dow_of_month(year, month, STOCK_FUTURES_EXPIRY_DOW)

    return expiry


def _last_dow_of_month(year: int, month: int, dow: int) -> date:
    """Find the last occurrence of a day-of-week in a given month.

    Args:
        year: Year
        month: Month (1-12)
        dow: Day of week (0=Monday, 3=Thursday)
    """
    _, last_day = monthrange(year, month)
    d = date(year, month, last_day)
    while d.weekday() != dow:
        d -= timedelta(days=1)
    return d


async def _find_futures_symbol(symbol: str, expiry: date) -> str | None:
    """Look up the Fyers futures symbol from the symbol master.

    Searches for a symbol matching the stock name, "FUT" segment, and expiry date.
    """
    try:
        from app.data_feed.symbol_master import symbol_master

        if not symbol_master.is_loaded:
            await symbol_master.load()

        if not symbol_master.is_loaded:
            return None

        # Search for futures contracts
        results = symbol_master.search(f"{symbol} FUT", limit=20)
        if not results:
            results = symbol_master.search(symbol, limit=20)

        for entry in results:
            fyers_sym = entry.get("s", "")  # Fyers symbol, e.g. "NSE:ADANIPORTS26APRFUT"
            seg = entry.get("g", "")        # Segment: EQ/FUT/OPT

            # Check it's a futures contract
            if seg != "FUT" and "FUT" not in fyers_sym.upper():
                continue

            # Check expiry matches — symbol master stores expiry as "DD Mon YYYY"
            entry_expiry_str = entry.get("x", "")
            if entry_expiry_str:
                from datetime import datetime as _dt
                try:
                    entry_expiry_date = _dt.strptime(entry_expiry_str, "%d %b %Y").date()
                except ValueError:
                    continue

                if entry_expiry_date == expiry:
                    return fyers_sym

            # If no expiry in data, try matching by symbol pattern
            if symbol.upper() in fyers_sym.upper() and "FUT" in fyers_sym.upper():
                return fyers_sym

    except Exception:
        logger.exception("Error searching symbol master for %s FUT", symbol)

    return None


async def _fetch_futures_ltp(fyers_symbol: str) -> float | None:
    """Fetch futures LTP from Redis cache or Fyers REST.

    Same pattern as option_resolver.fetch_option_premium.
    """
    # Try Redis cache first
    from app.core.redis import get_cached_price

    cached = await get_cached_price(fyers_symbol)
    if cached:
        ltp = cached.get("ltp")
        if ltp and float(ltp) > 0:
            return float(ltp)

    # Fall back to Fyers REST
    try:
        from app.core.redis import get_redis

        r = get_redis()
        token = await r.get("fyers:access_token")
        if not token:
            return None

        from app.data_feed.fyers_client import FyersClient

        client = FyersClient(access_token=token)
        try:
            quotes = await client.get_quotes([fyers_symbol])
        finally:
            await client.close()

        if quotes and quotes.get("s") == "ok":
            for q in quotes.get("d", []):
                v = q.get("v", {})
                ltp = v.get("lp") or v.get("ltp")
                if ltp and float(ltp) > 0:
                    return float(ltp)
    except Exception:
        logger.exception("Failed REST fallback for futures LTP: %s", fyers_symbol)

    return None


async def _get_lot_size(symbol: str, fyers_symbol: str) -> int:
    """Get lot size for a stock futures contract.

    Tries: 1) symbol master metadata, 2) stock_fundamentals table, 3) default.
    """
    # Try symbol master
    try:
        from app.data_feed.symbol_master import symbol_master

        if symbol_master.is_loaded:
            results = symbol_master.search(fyers_symbol, limit=5)
            for entry in results:
                lot = entry.get("l")  # lot size in symbol master
                if lot and int(lot) > 0:
                    return int(lot)
    except Exception:
        pass

    # Try stock_fundamentals table
    try:
        from app.core.database import async_session_factory
        from app.models.fundamental_data import StockFundamental
        from sqlalchemy import select

        async with async_session_factory() as session:
            result = await session.execute(
                select(StockFundamental.lot_size).where(
                    StockFundamental.symbol == symbol
                )
            )
            lot = result.scalar_one_or_none()
            if lot and lot > 0:
                return lot
    except Exception:
        pass

    # Default: 1 lot = 1 unit (conservative)
    logger.warning("Could not determine lot size for %s, defaulting to 1", symbol)
    return 1


def find_index_futures_expiry(index: str, from_date: date) -> date:
    """Find the near-month expiry date for an index futures contract.

    Uses INDEX_FUTURES_EXPIRY_DOW (NSE indices → last Tuesday, SENSEX → last Thursday).
    If from_date is already past this month's expiry, returns next month's.
    """
    expiry_dow = INDEX_FUTURES_EXPIRY_DOW[index]
    year, month = from_date.year, from_date.month
    expiry = _last_dow_of_month(year, month, expiry_dow)
    if from_date > expiry:
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        expiry = _last_dow_of_month(year, month, expiry_dow)
    return expiry


async def resolve_index_futures_symbol(
    index: str, from_date: date | None = None
) -> tuple[str, date] | None:
    """Resolve an index to its near-month futures (Fyers symbol, expiry date).

    Used by strategy_runner to subscribe the correct futures contract for VWAP
    volume sourcing. Tries near-month; falls back to next month if not found.

    Returns (fyers_symbol, expiry_date) or None if the symbol master lacks the entry.
    """
    today = from_date or now_ist().date()
    expiry = find_index_futures_expiry(index, today)

    fyers_symbol = await _find_futures_symbol(index, expiry)
    if fyers_symbol is None:
        next_expiry = find_index_futures_expiry(expiry + timedelta(days=1), expiry + timedelta(days=1))
        fyers_symbol = await _find_futures_symbol(index, next_expiry)
        if fyers_symbol is None:
            logger.warning(
                "Could not resolve index futures for %s (tried %s and %s)",
                index, expiry, next_expiry,
            )
            return None
        expiry = next_expiry

    logger.info("Resolved index futures: %s → %s (expires %s)", index, fyers_symbol, expiry)
    return fyers_symbol, expiry
