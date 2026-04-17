"""Option resolver — translates index-level signals into tradeable option contracts.

Given an index symbol, price, and signal direction, this module:
1. Selects the ATM or 1-strike ITM strike price
2. Finds the nearest expiry (weekly for NIFTY/SENSEX, monthly for others)
3. Looks up the Fyers option symbol via the symbol master
4. Fetches the option premium (LTP) from Redis cache or Fyers REST
5. Computes SL and target based on the option premium

This module is called as a post-processing step by the strategy runner,
ONLY for signals with instrument_type == OPTION. Futures and equity signals
bypass this entirely.
"""

import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.constants import (
    IST,
    MONTHLY_EXPIRY_DOW,
    MONTHLY_ONLY_INDICES,
    OPTION_EXCHANGE,
    PREMIUM_RANGE_MAX,
    PREMIUM_RANGE_MIN,
    STRIKE_GAPS,
    WEEKLY_EXPIRY_DAYS,
)
from app.core.enums import SignalType
from app.core.utils import now_ist

logger = logging.getLogger(__name__)


@dataclass
class OptionResolution:
    """Result of resolving an index-level signal to a specific option contract."""

    strike_price: float
    expiry_date: date
    option_premium: float
    fyers_option_symbol: str
    sl_price: float
    target_price: float


# ---------------------------------------------------------------------------
# Strike selection
# ---------------------------------------------------------------------------


def select_strike(
    index_price: float,
    signal_type: SignalType,
    strike_gap: int,
) -> tuple[float, float]:
    """Select ATM and 1-strike ITM strikes for the given index price.

    Returns:
        (atm_strike, itm_strike)
    """
    atm = round(index_price / strike_gap) * strike_gap

    if signal_type == SignalType.BUY_CE:
        # ITM for calls = one strike below ATM
        itm = atm - strike_gap
    else:
        # ITM for puts = one strike above ATM
        itm = atm + strike_gap

    return atm, itm


# ---------------------------------------------------------------------------
# Expiry selection
# ---------------------------------------------------------------------------


def select_expiry(symbol: str) -> date:
    """Select the nearest tradeable expiry for the given index.

    - NIFTY: nearest weekly Tuesday
    - SENSEX: nearest weekly Thursday
    - BANKNIFTY, FINNIFTY, MIDCPNIFTY: last Tuesday of current/next month
    """
    today = now_ist().date()

    if symbol in MONTHLY_ONLY_INDICES:
        return _next_monthly_expiry(symbol, today)

    # Weekly expiry
    expiry_dow = WEEKLY_EXPIRY_DAYS[symbol]
    return _next_weekly_expiry(today, expiry_dow)


def _next_weekly_expiry(today: date, target_dow: int) -> date:
    """Find the next occurrence of target_dow (0=Mon..6=Sun), including today."""
    days_ahead = (target_dow - today.weekday()) % 7
    expiry = today + timedelta(days=days_ahead)
    # If today IS the expiry day, use it (still tradeable during market hours)
    return expiry


def _next_monthly_expiry(symbol: str, today: date) -> date:
    """Find the last Tuesday (NSE) or Thursday (BSE) of the current or next month."""
    exchange = OPTION_EXCHANGE.get(symbol, "NSE")
    expiry_dow = MONTHLY_EXPIRY_DOW.get(exchange, 1)  # default Tuesday

    # Try current month first
    expiry = _last_dow_of_month(today.year, today.month, expiry_dow)
    if expiry >= today:
        return expiry

    # Current month's expiry has passed — use next month
    if today.month == 12:
        return _last_dow_of_month(today.year + 1, 1, expiry_dow)
    return _last_dow_of_month(today.year, today.month + 1, expiry_dow)


def _last_dow_of_month(year: int, month: int, target_dow: int) -> date:
    """Find the last occurrence of a day-of-week in a given month."""
    last_day = monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != target_dow:
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Symbol master lookup
# ---------------------------------------------------------------------------


async def find_option_symbol(
    symbol: str,
    strike: float,
    expiry: date,
    option_type: str,
) -> str | None:
    """Look up the Fyers option symbol from the symbol master.

    Args:
        symbol: Index name (e.g. "NIFTY")
        strike: Strike price (e.g. 24000)
        expiry: Expiry date
        option_type: "CE" or "PE"

    Returns:
        Fyers symbol string (e.g. "NSE:NIFTY2541524000CE") or None
    """
    from app.data_feed.symbol_master import symbol_master

    if not symbol_master.is_loaded:
        await symbol_master.load()

    if not symbol_master.is_loaded:
        logger.warning("Symbol master not available — cannot resolve option symbol")
        return None

    # Search for matching options
    query = f"{symbol} {int(strike)}{option_type}"
    results = symbol_master.search(query, limit=20)

    # Format expiry for comparison (symbol master stores "DD Mon YYYY", e.g. "15 Apr 2026")
    expiry_str = expiry.strftime("%d %b %Y")
    # Also try without leading zero for day
    expiry_str_alt = expiry_str.lstrip("0")

    for r in results:
        if (
            r.get("g") == "OPT"
            and r.get("t") == option_type
            and r.get("k") == strike
        ):
            # Check expiry match
            sym_expiry = r.get("x", "")
            if sym_expiry == expiry_str or sym_expiry == expiry_str_alt:
                return r["s"]

    # If exact expiry match fails, try the closest future expiry with matching strike
    # This handles holiday-shifted expiry dates
    for r in results:
        if (
            r.get("g") == "OPT"
            and r.get("t") == option_type
            and r.get("k") == strike
        ):
            return r["s"]

    logger.warning(
        "No symbol master match for %s %s %s expiry %s",
        symbol, int(strike), option_type, expiry_str,
    )
    return None


# ---------------------------------------------------------------------------
# Premium fetch
# ---------------------------------------------------------------------------


async def fetch_option_premium(fyers_symbol: str) -> float | None:
    """Fetch the current LTP for an option contract.

    Tries Redis cache first, then falls back to Fyers REST quotes API.
    """
    from app.core.redis import get_cached_price

    # 1. Try Redis cache (option may already be tracked by websocket feed)
    cached = await get_cached_price(fyers_symbol)
    if cached and cached.get("ltp", 0) > 0:
        return float(cached["ltp"])

    # 2. Fallback: Fyers REST quotes
    return await _fetch_premium_rest(fyers_symbol)


async def _fetch_premium_rest(fyers_symbol: str) -> float | None:
    """Fetch option premium via Fyers REST API."""
    from app.core.redis import get_redis
    from app.data_feed.fyers_client import FyersClient

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers access token — cannot fetch option premium")
        return None

    client = FyersClient(access_token=token)
    try:
        result = await client.get_quotes([fyers_symbol])
        quotes = result.get("d", [])
        if quotes:
            ltp = quotes[0].get("v", {}).get("lp", 0)
            if ltp > 0:
                return float(ltp)
        logger.warning("Fyers quotes returned no LTP for %s", fyers_symbol)
        return None
    except Exception:
        logger.exception("Failed to fetch premium for %s", fyers_symbol)
        return None
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


async def resolve_option_details(
    symbol: str,
    index_price: float,
    signal_type: SignalType,
    sl_pct: float,
    rr_multiplier: float = 1.5,
    index_sl: float | None = None,
    index_target: float | None = None,
) -> OptionResolution | None:
    """Resolve an index-level signal into a specific option contract with premium-based SL/target.

    Args:
        symbol: Index name (e.g. "NIFTY")
        index_price: Current underlying index price
        signal_type: BUY_CE or BUY_PE
        sl_pct: Stop-loss percentage on premium (fallback if no index levels)
        rr_multiplier: Risk-reward multiplier for target (fallback)
        index_sl: Index-level stop-loss from market structure (e.g. VWAP lower band)
        index_target: Index-level target from market structure (e.g. PDH)

    Returns:
        OptionResolution if successful, None if premium unavailable
    """
    strike_gap = STRIKE_GAPS.get(symbol)
    if strike_gap is None:
        logger.error("No strike gap configured for %s", symbol)
        return None

    option_type = "CE" if signal_type == SignalType.BUY_CE else "PE"

    # 1. Select strike
    atm_strike, itm_strike = select_strike(index_price, signal_type, strike_gap)

    # 2. Select expiry
    expiry = select_expiry(symbol)

    # 3. Try ATM first, then ITM
    resolution = await _try_strike(
        symbol, atm_strike, expiry, option_type, sl_pct, rr_multiplier, "ATM",
        index_price=index_price, index_sl=index_sl, index_target=index_target,
    )
    if resolution is not None:
        return resolution

    resolution = await _try_strike(
        symbol, itm_strike, expiry, option_type, sl_pct, rr_multiplier, "ITM",
        index_price=index_price, index_sl=index_sl, index_target=index_target,
    )
    if resolution is not None:
        return resolution

    logger.warning(
        "Could not resolve option for %s %s at index price %.2f",
        symbol, signal_type, index_price,
    )
    return None


async def _try_strike(
    symbol: str,
    strike: float,
    expiry: date,
    option_type: str,
    sl_pct: float,
    rr_multiplier: float,
    label: str,
    index_price: float = 0,
    index_sl: float | None = None,
    index_target: float | None = None,
) -> OptionResolution | None:
    """Attempt to resolve a single strike: find symbol, fetch premium, compute SL/target."""
    fyers_symbol = await find_option_symbol(symbol, strike, expiry, option_type)
    if fyers_symbol is None:
        return None

    premium = await fetch_option_premium(fyers_symbol)
    if premium is None or premium <= 0:
        return None

    # Log if premium is outside preferred range (soft warning, not a blocker)
    if premium < PREMIUM_RANGE_MIN or premium > PREMIUM_RANGE_MAX:
        logger.info(
            "%s strike %.0f premium %.2f outside preferred range [%.0f-%.0f] (%s)",
            symbol, strike, premium, PREMIUM_RANGE_MIN, PREMIUM_RANGE_MAX, label,
        )

    # Compute SL and target on the option premium
    sl_price, target_price = _compute_premium_sl_target(
        premium=premium,
        option_type=option_type,
        sl_pct=sl_pct,
        rr_multiplier=rr_multiplier,
        label=label,
        index_price=index_price,
        index_sl=index_sl,
        index_target=index_target,
    )

    return OptionResolution(
        strike_price=strike,
        expiry_date=expiry,
        option_premium=premium,
        fyers_option_symbol=fyers_symbol,
        sl_price=round(sl_price, 2),
        target_price=round(target_price, 2),
    )


def _compute_premium_sl_target(
    premium: float,
    option_type: str,
    sl_pct: float,
    rr_multiplier: float,
    label: str,
    index_price: float = 0,
    index_sl: float | None = None,
    index_target: float | None = None,
) -> tuple[float, float]:
    """Compute premium-level SL and target.

    If index-level SL/target are provided, uses delta approximation to convert
    index point movements into premium movements. Otherwise falls back to
    fixed-percentage computation.

    Delta approximation:
        ATM options: delta ~0.50 (1 index point ≈ 0.50 premium point)
        ITM options: delta ~0.60
    """
    if index_sl is not None and index_target is not None and index_price > 0:
        delta = 0.50 if label == "ATM" else 0.60

        if option_type == "CE":
            sl_price = premium - delta * (index_price - index_sl)
            target_price = premium + delta * (index_target - index_price)
        else:  # PE
            sl_price = premium - delta * (index_sl - index_price)
            target_price = premium + delta * (index_price - index_target)

        # Safety: if delta math produces invalid values, fall back to pct
        if sl_price > 0 and sl_price < premium and target_price > premium:
            logger.info(
                "Delta-based SL/target: premium=%.2f SL=%.2f target=%.2f "
                "(index SL=%.2f, index target=%.2f, delta=%.2f)",
                premium, sl_price, target_price, index_sl, index_target, delta,
            )
            return sl_price, target_price

        logger.info(
            "Delta-based SL/target invalid (sl=%.2f, tgt=%.2f) — "
            "falling back to fixed pct for %s",
            sl_price, target_price, label,
        )

    # Fallback: fixed percentage on premium
    sl_price = premium * (1 - sl_pct)
    target_price = premium * (1 + sl_pct * rr_multiplier)
    return sl_price, target_price
