"""Multi-day stock trend direction from daily candles.

Computes a composite trend score from 6 factors using 5/20-day lookbacks.
Pure function — no DB or Redis access.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.indicators.candle_patterns import Candle


@dataclass
class StockTrend:
    direction: str  # "BULLISH" / "BEARISH" / "NEUTRAL"
    strength: str  # "STRONG" / "MODERATE" / "WEAK"
    score: float  # -1.0 (strong bearish) to +1.0 (strong bullish)
    components: dict[str, float]


_MIN_BARS = 10


def compute_stock_trend(daily_candles: list[Candle]) -> StockTrend:
    """Compute multi-day trend from daily OHLCV candles.

    Returns NEUTRAL/WEAK if fewer than 10 bars available.
    """
    if len(daily_candles) < _MIN_BARS:
        return StockTrend(
            direction="NEUTRAL",
            strength="WEAK",
            score=0.0,
            components={},
        )

    closes = [c.close for c in daily_candles]

    components = {
        "price_vs_20dma": _price_vs_20dma(closes),
        "dma_crossover": _dma_crossover(closes),
        "hh_hl_pattern": _hh_hl_pattern(daily_candles),
        "adr_trend": _adr_trend(daily_candles),
        "close_position": _close_position(daily_candles),
        "rs_momentum": _rs_momentum(closes),
    }

    score = (
        components["price_vs_20dma"] * 0.25
        + components["dma_crossover"] * 0.20
        + components["hh_hl_pattern"] * 0.20
        + components["adr_trend"] * 0.10
        + components["close_position"] * 0.15
        + components["rs_momentum"] * 0.10
    )

    # Convert 0-1 factor scores to -1 to +1 range
    score = (score - 0.5) * 2.0
    score = max(-1.0, min(1.0, score))

    if score > 0.3:
        direction = "BULLISH"
    elif score < -0.3:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    abs_score = abs(score)
    if abs_score > 0.6:
        strength = "STRONG"
    elif abs_score > 0.3:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    return StockTrend(
        direction=direction,
        strength=strength,
        score=round(score, 4),
        components={k: round(v, 4) for k, v in components.items()},
    )


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _price_vs_20dma(closes: list[float]) -> float:
    """0-1 score: 1.0 = well above 20 DMA, 0.0 = well below."""
    dma = _sma(closes, min(20, len(closes)))
    if dma is None or dma <= 0:
        return 0.5
    price = closes[-1]
    pct_from_dma = (price - dma) / dma
    # ±5% maps to 0-1 range
    return max(0.0, min(1.0, 0.5 + pct_from_dma * 10))


def _dma_crossover(closes: list[float]) -> float:
    """0-1 score: 1.0 = 5 DMA well above 20 DMA, 0.0 = well below."""
    dma5 = _sma(closes, min(5, len(closes)))
    dma20 = _sma(closes, min(20, len(closes)))
    if dma5 is None or dma20 is None or dma20 <= 0:
        return 0.5
    pct_diff = (dma5 - dma20) / dma20
    return max(0.0, min(1.0, 0.5 + pct_diff * 15))


def _hh_hl_pattern(candles: list[Candle]) -> float:
    """0-1 score from higher-highs/higher-lows vs lower-highs/lower-lows over last 5 bars."""
    recent = candles[-6:] if len(candles) >= 6 else candles
    if len(recent) < 2:
        return 0.5

    bullish = 0
    bearish = 0
    for i in range(1, len(recent)):
        if recent[i].high > recent[i - 1].high:
            bullish += 1
        elif recent[i].high < recent[i - 1].high:
            bearish += 1
        if recent[i].low > recent[i - 1].low:
            bullish += 1
        elif recent[i].low < recent[i - 1].low:
            bearish += 1

    total = bullish + bearish
    if total == 0:
        return 0.5
    return bullish / total


def _adr_trend(candles: list[Candle]) -> float:
    """0-1 score: expanding range in trend direction = higher score.

    Compares 5-day ADR to 20-day ADR. Expanding range is bullish when
    recent closes trend up, bearish when they trend down.
    """
    if len(candles) < 6:
        return 0.5

    def _avg_range(subset: list[Candle]) -> float:
        if not subset:
            return 0.0
        total = sum((c.high - c.low) / c.close for c in subset if c.close > 0)
        return total / len(subset) if subset else 0.0

    recent_5 = candles[-5:]
    full_20 = candles[-min(20, len(candles)):]
    adr5 = _avg_range(recent_5)
    adr20 = _avg_range(full_20)

    if adr20 <= 0:
        return 0.5

    expansion_ratio = adr5 / adr20

    # Determine recent direction from closes
    recent_direction = 1 if recent_5[-1].close > recent_5[0].close else -1

    if expansion_ratio > 1.0:
        # Range expanding — good if in the trend direction
        strength = min(1.0, (expansion_ratio - 1.0) * 5)
        if recent_direction > 0:
            return 0.5 + strength * 0.5  # bullish expansion
        else:
            return 0.5 - strength * 0.5  # bearish expansion
    else:
        # Range contracting — mild mean reversion
        return 0.5


def _close_position(candles: list[Candle]) -> float:
    """0-1 score: avg position of close within daily range over last 5 bars."""
    recent = candles[-5:]
    if not recent:
        return 0.5

    positions = []
    for c in recent:
        rng = c.high - c.low
        if rng > 0:
            positions.append((c.close - c.low) / rng)
    if not positions:
        return 0.5
    return sum(positions) / len(positions)


def _rs_momentum(closes: list[float]) -> float:
    """0-1 score: is the stock's rate-of-change improving over 10 days?

    Compares the 5-day return at the end vs middle of the lookback window.
    """
    lookback = min(10, len(closes))
    if lookback < 6:
        return 0.5

    window = closes[-lookback:]
    mid = lookback // 2

    first_half_ret = (window[mid] - window[0]) / window[0] if window[0] > 0 else 0
    second_half_ret = (window[-1] - window[mid]) / window[mid] if window[mid] > 0 else 0

    if second_half_ret > first_half_ret:
        diff = second_half_ret - first_half_ret
        return min(1.0, 0.5 + diff * 10)
    elif second_half_ret < first_half_ret:
        diff = first_half_ret - second_half_ret
        return max(0.0, 0.5 - diff * 10)
    return 0.5
