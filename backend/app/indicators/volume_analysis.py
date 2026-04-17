"""Volume analysis indicators for CAN SLIM.

Detects volume breakouts (current volume significantly above average)
which are a key CAN SLIM entry confirmation signal.
"""


def compute_avg_volume(daily_volumes: list[int], period: int = 20) -> int:
    """Compute average daily volume over the given period.

    Args:
        daily_volumes: List of daily volumes (most recent last).
        period: Number of days to average over.

    Returns 0 if insufficient data.
    """
    if not daily_volumes or len(daily_volumes) < period:
        if daily_volumes:
            return int(sum(daily_volumes) / len(daily_volumes))
        return 0
    return int(sum(daily_volumes[-period:]) / period)


def is_volume_breakout(
    current_volume: int,
    avg_volume_20d: int,
    threshold: float = 1.5,
) -> bool:
    """Check if current volume exceeds threshold * 20-day average.

    CAN SLIM requires breakout on above-average volume (typically > 1.5x).

    Args:
        current_volume: Today's volume (or current session volume).
        avg_volume_20d: 20-day average daily volume.
        threshold: Multiplier threshold (default 1.5x).
    """
    if avg_volume_20d <= 0:
        return False
    return current_volume >= avg_volume_20d * threshold


def volume_ratio(current_volume: int, avg_volume_20d: int) -> float:
    """Compute the ratio of current volume to 20-day average.

    Returns 0.0 if average is zero.
    """
    if avg_volume_20d <= 0:
        return 0.0
    return current_volume / avg_volume_20d
