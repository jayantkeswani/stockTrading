"""Strike and expiry selection for backtest replay.

Adapts the live option_resolver logic to work with historical dates instead
of the current wall clock. The symbol master is still used for symbol lookups
(it contains all active + recent contracts).

Key difference from the live resolver:
- `select_expiry_as_of(symbol, as_of_date)` replaces `select_expiry()` which
  calls `now_ist().date()` internally.
- Premium-range enforcement (150-400 INR) is deferred to the exit_simulator
  which has the actual historical premium from the option's 1m candles.
"""

import logging
from calendar import monthrange
from datetime import date, timedelta

from app.core.constants import (
    MONTHLY_ONLY_INDICES,
    OPTION_EXCHANGE,
    MONTHLY_EXPIRY_DOW,
    STRIKE_GAPS,
    WEEKLY_EXPIRY_DAYS,
)
from app.core.enums import SignalType
from app.services.option_resolver import select_strike

logger = logging.getLogger(__name__)


def select_expiry_as_of(symbol: str, as_of_date: date) -> date:
    """Select the nearest tradeable expiry as seen from as_of_date.

    Mirrors option_resolver.select_expiry() but accepts a historical date
    instead of using now_ist().
    """
    if symbol in MONTHLY_ONLY_INDICES:
        return _next_monthly_expiry_as_of(symbol, as_of_date)

    expiry_dow = WEEKLY_EXPIRY_DAYS[symbol]
    return _next_weekly_expiry_as_of(as_of_date, expiry_dow)


def _next_weekly_expiry_as_of(as_of_date: date, target_dow: int) -> date:
    days_ahead = (target_dow - as_of_date.weekday()) % 7
    return as_of_date + timedelta(days=days_ahead)


def _next_monthly_expiry_as_of(symbol: str, as_of_date: date) -> date:
    exchange = OPTION_EXCHANGE.get(symbol, "NSE")
    expiry_dow = MONTHLY_EXPIRY_DOW.get(exchange, 1)

    expiry = _last_dow_of_month(as_of_date.year, as_of_date.month, expiry_dow)
    if expiry >= as_of_date:
        return expiry

    if as_of_date.month == 12:
        return _last_dow_of_month(as_of_date.year + 1, 1, expiry_dow)
    return _last_dow_of_month(as_of_date.year, as_of_date.month + 1, expiry_dow)


def _last_dow_of_month(year: int, month: int, target_dow: int) -> date:
    last_day = monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != target_dow:
        d -= timedelta(days=1)
    return d


async def resolve_option_symbol(
    symbol: str,
    index_price: float,
    signal_type: SignalType,
    as_of_date: date,
) -> tuple[float, date, str] | None:
    """Resolve strike + expiry + Fyers symbol for a backtest signal.

    Tries ATM first; if the symbol master lookup fails, tries 1-ITM.
    Returns (strike, expiry, fyers_option_symbol) or None.
    """
    from app.services.option_resolver import find_option_symbol

    strike_gap = STRIKE_GAPS.get(symbol, 50)
    atm, itm = select_strike(index_price, signal_type, strike_gap)
    expiry = select_expiry_as_of(symbol, as_of_date)
    option_type = "CE" if signal_type == SignalType.BUY_CE else "PE"

    for candidate_strike in (atm, itm):
        fyers_sym = await find_option_symbol(symbol, candidate_strike, expiry, option_type)
        if fyers_sym:
            return candidate_strike, expiry, fyers_sym

    logger.debug(
        "Could not resolve option symbol for %s %s %.0f (as_of %s)",
        symbol, option_type, index_price, as_of_date,
    )
    return None
