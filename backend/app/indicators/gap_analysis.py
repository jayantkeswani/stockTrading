"""Gap analysis — detects opening gaps and continuation patterns."""

from app.indicators.candle_patterns import Candle


def detect_gap(
    today_open: float, prev_close: float, min_gap_pct: float = 0.5
) -> dict | None:
    """Detect a gap between previous close and today's open.

    Returns {"direction": "UP"|"DOWN", "gap_pct": float, "gap_level": prev_close}
    or None if gap is below threshold.
    """
    if prev_close <= 0:
        return None

    gap_pct = ((today_open - prev_close) / prev_close) * 100

    if abs(gap_pct) < min_gap_pct:
        return None

    return {
        "direction": "UP" if gap_pct > 0 else "DOWN",
        "gap_pct": abs(gap_pct),
        "gap_level": prev_close,
    }


def is_gap_continuation(
    candles_5m: list[Candle], gap_direction: str, gap_level: float
) -> bool:
    """Check if price continues in the gap direction after the first 3 five-min candles.

    Requires at least 4 candles (3 formation + 1 confirmation).
    After the first 3 candles, all subsequent closes must hold above (gap up)
    or below (gap down) the gap level.
    """
    if len(candles_5m) < 4:
        return False

    for c in candles_5m[3:]:
        if gap_direction == "UP" and c.close < gap_level:
            return False
        if gap_direction == "DOWN" and c.close > gap_level:
            return False

    return True
