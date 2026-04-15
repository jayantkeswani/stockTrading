"""Utility functions for time handling and market hours."""

from datetime import datetime, time

from app.core.constants import (
    DEAD_ZONE_END,
    DEAD_ZONE_START,
    IST,
    MARKET_CLOSE,
    MARKET_OPEN,
    POSITION_CLOSE_DEADLINE,
    WINDOW_1_END,
    WINDOW_1_START,
    WINDOW_2_END,
    WINDOW_2_START,
)


def now_ist() -> datetime:
    """Get current time in IST."""
    return datetime.now(IST)


def is_market_open() -> bool:
    """Check if the market is currently open."""
    current = now_ist()
    if current.weekday() >= 5:  # Saturday/Sunday
        return False
    t = current.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_in_trading_window() -> bool:
    """Check if current time is in an active trading window (Strategy 2)."""
    t = now_ist().time()
    return _in_window_1(t) or _in_window_2(t)


def is_in_dead_zone() -> bool:
    """Check if current time is in the dead zone (11:30 AM - 1:30 PM)."""
    t = now_ist().time()
    return DEAD_ZONE_START <= t <= DEAD_ZONE_END


def is_past_close_deadline() -> bool:
    """Check if we're past the position close deadline (3:15 PM)."""
    t = now_ist().time()
    return t >= POSITION_CLOSE_DEADLINE


def time_to_market_close_minutes() -> int:
    """Minutes remaining until market close."""
    current = now_ist()
    close_dt = current.replace(
        hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0
    )
    delta = close_dt - current
    return max(0, int(delta.total_seconds() / 60))


def _in_window_1(t: time) -> bool:
    return WINDOW_1_START <= t <= WINDOW_1_END


def _in_window_2(t: time) -> bool:
    return WINDOW_2_START <= t <= WINDOW_2_END
