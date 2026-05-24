"""Utility functions for time handling and market hours."""

from datetime import date, datetime, time

from app.core.constants import (
    DEAD_ZONE_END,
    DEAD_ZONE_START,
    IST,
    MARKET_CLOSE,
    MARKET_OPEN,
    NSE_HOLIDAYS,
    POSITION_CLOSE_DEADLINE,
    WINDOW_1_END,
    WINDOW_1_START,
    WINDOW_2_END,
    WINDOW_2_START,
)


def now_ist() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def is_trading_day(d: date) -> bool:
    """Return True if d is an NSE trading day (weekday and not a listed holiday)."""
    from app.config import settings
    if settings.market_mode == "simulated":
        return True
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def is_market_open(as_of: datetime | None = None) -> bool:
    """Check if the market is currently open."""
    from app.config import settings
    if settings.market_mode == "simulated":
        return True
    current = as_of or now_ist()
    if not is_trading_day(current.date()):
        return False
    t = current.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_in_trading_window(as_of: datetime | None = None) -> bool:
    """Check if current time is in an active trading window (Strategy 2).

    Pass as_of for backtest replay; omit to use the current IST time.
    """
    t = (as_of or now_ist()).time()
    return _in_window_1(t) or _in_window_2(t)


def get_window_state(as_of: datetime | None = None) -> str:
    """Return 'IN_WINDOW', 'DEAD_ZONE', or 'OUT_OF_WINDOW' for the given time."""
    t = (as_of or now_ist()).time()
    if _in_window_1(t) or _in_window_2(t):
        return "IN_WINDOW"
    if DEAD_ZONE_START <= t <= DEAD_ZONE_END:
        return "DEAD_ZONE"
    return "OUT_OF_WINDOW"


def is_in_dead_zone(as_of: datetime | None = None) -> bool:
    """Check if current time is in the dead zone (11:30 AM - 1:30 PM)."""
    t = (as_of or now_ist()).time()
    return DEAD_ZONE_START <= t <= DEAD_ZONE_END


def is_past_close_deadline(as_of: datetime | None = None) -> bool:
    """Check if we're past the position close deadline (3:15 PM).

    Pass as_of for backtest replay; omit to use the current IST time.
    """
    from app.config import settings
    if settings.market_mode == "simulated":
        return False
    t = (as_of or now_ist()).time()
    return t >= POSITION_CLOSE_DEADLINE


def time_to_market_close_minutes(as_of: datetime | None = None) -> int:
    """Minutes remaining until market close."""
    current = as_of or now_ist()
    close_dt = current.replace(
        hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0
    )
    delta = close_dt - current
    return max(0, int(delta.total_seconds() / 60))


def is_in_custom_trading_window(
    as_of: datetime | None = None,
    windows: list[tuple[time, time]] | None = None,
) -> bool:
    """Check if time falls within any of the given trading windows.

    If windows is None or empty, returns True (no window restriction).
    """
    if not windows:
        return True
    t = (as_of or now_ist()).time()
    return any(start <= t <= end for start, end in windows)


def get_custom_window_state(
    as_of: datetime | None = None,
    windows: list[tuple[time, time]] | None = None,
    dead_zone: tuple[time, time] | None = None,
) -> str:
    """Return 'IN_WINDOW', 'DEAD_ZONE', or 'OUT_OF_WINDOW' for custom windows."""
    if not windows:
        return "IN_WINDOW"
    t = (as_of or now_ist()).time()
    if any(start <= t <= end for start, end in windows):
        return "IN_WINDOW"
    if dead_zone and dead_zone[0] <= t <= dead_zone[1]:
        return "DEAD_ZONE"
    return "OUT_OF_WINDOW"


def _in_window_1(t: time) -> bool:
    return WINDOW_1_START <= t <= WINDOW_1_END


def _in_window_2(t: time) -> bool:
    return WINDOW_2_START <= t <= WINDOW_2_END
