"""Average Daily Range (ADR) — measures typical intraday price movement."""

from app.indicators.candle_patterns import Candle


def compute_adr(daily_candles: list[Candle], period: int = 20) -> float:
    """Mean of (high - low) / close * 100 over the last `period` days.

    Returns 0.0 if no valid candles.
    """
    candles = daily_candles[-period:]
    if not candles:
        return 0.0
    total = 0.0
    count = 0
    for c in candles:
        if c.close > 0:
            total += (c.high - c.low) / c.close * 100
            count += 1
    return total / count if count > 0 else 0.0


def adr_qualifies(adr_pct: float, min_adr: float = 1.5) -> bool:
    """Return True if stock moves enough intraday to be worth trading."""
    return adr_pct >= min_adr
