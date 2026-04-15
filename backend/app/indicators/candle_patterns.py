"""Candlestick pattern detection for entry confirmation."""

from dataclasses import dataclass


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: int


def is_bullish_engulfing(prev: Candle, curr: Candle) -> bool:
    """Previous candle bearish, current candle bullish and engulfs previous body."""
    prev_bearish = prev.close < prev.open
    curr_bullish = curr.close > curr.open
    engulfs = curr.open <= prev.close and curr.close >= prev.open
    return prev_bearish and curr_bullish and engulfs


def is_bearish_engulfing(prev: Candle, curr: Candle) -> bool:
    """Previous candle bullish, current candle bearish and engulfs previous body."""
    prev_bullish = prev.close > prev.open
    curr_bearish = curr.close < curr.open
    engulfs = curr.open >= prev.close and curr.close <= prev.open
    return prev_bullish and curr_bearish and engulfs


def is_bullish_pin_bar(candle: Candle) -> bool:
    """Long lower wick, small body at top. Lower wick >= 2x body."""
    body = abs(candle.close - candle.open)
    lower_wick = min(candle.open, candle.close) - candle.low
    upper_wick = candle.high - max(candle.open, candle.close)
    if body == 0:
        return False
    return lower_wick >= 2 * body and upper_wick <= body


def is_bearish_pin_bar(candle: Candle) -> bool:
    """Long upper wick, small body at bottom. Upper wick >= 2x body."""
    body = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    if body == 0:
        return False
    return upper_wick >= 2 * body and lower_wick <= body


def is_doji(candle: Candle, threshold_pct: float = 0.05) -> bool:
    """Body is very small relative to the total range."""
    total_range = candle.high - candle.low
    if total_range == 0:
        return True
    body = abs(candle.close - candle.open)
    return (body / total_range) <= threshold_pct


def is_bullish_reversal(candles: list[Candle]) -> bool:
    """Check for any bullish reversal pattern in the last 2-3 candles."""
    if len(candles) < 2:
        return False
    prev, curr = candles[-2], candles[-1]
    return (
        is_bullish_engulfing(prev, curr)
        or is_bullish_pin_bar(curr)
        or (is_doji(prev) and curr.close > curr.open)
    )


def is_bearish_reversal(candles: list[Candle]) -> bool:
    """Check for any bearish reversal pattern in the last 2-3 candles."""
    if len(candles) < 2:
        return False
    prev, curr = candles[-2], candles[-1]
    return (
        is_bearish_engulfing(prev, curr)
        or is_bearish_pin_bar(curr)
        or (is_doji(prev) and curr.close < curr.open)
    )


def average_volume(candles: list[Candle], periods: int = 20) -> float:
    """Calculate average volume over last N candles."""
    if not candles:
        return 0
    subset = candles[-periods:]
    return sum(c.volume for c in subset) / len(subset)
