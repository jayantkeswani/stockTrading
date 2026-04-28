"""Average True Range (ATR) — measures volatility via true range averaging."""

from app.indicators.candle_patterns import Candle


def compute_atr(candles: list[Candle], period: int = 14) -> float:
    """Compute ATR over the last `period` candles.

    True Range = max(H-L, |H-prev_close|, |L-prev_close|).
    Returns 0.0 if fewer than 2 candles.
    """
    if len(candles) < 2:
        return 0.0

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        prev_c = candles[i - 1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    recent = true_ranges[-period:]
    return sum(recent) / len(recent)
