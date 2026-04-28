"""Relative Volume (RVOL) — time-of-day normalized volume indicator.

Compares current volume to a historical baseline for the same time-of-day bucket,
revealing whether activity is abnormally high for *right now* (not just vs daily avg).
"""

import json
from collections import defaultdict
from datetime import date

from app.indicators.candle_patterns import Candle


def build_volume_profile(
    historical_5m: dict[date, list[Candle]],
) -> dict[str, float]:
    """Build average-volume-per-time-bucket from historical 5-min candles.

    Args:
        historical_5m: mapping of date → list of 5-min Candle objects for that day,
            ordered by time. Each day has ~75 buckets (9:15–15:30 in 5-min steps).
            Bucket index 0 = 09:15, index 1 = 09:20, ..., index 74 = 15:25.

    Returns:
        dict mapping bucket index (as string, e.g. "0", "1", ...) to average volume.
    """
    bucket_totals: dict[int, list[int]] = defaultdict(list)
    for _day, candles in historical_5m.items():
        for i, c in enumerate(candles):
            bucket_totals[i].append(c.volume)

    profile: dict[str, float] = {}
    for bucket, volumes in sorted(bucket_totals.items()):
        profile[str(bucket)] = sum(volumes) / len(volumes) if volumes else 0.0
    return profile


def compute_rvol(current_volume: int, bucket_avg: float) -> float:
    """Current volume divided by the historical average for this time bucket.

    Returns 0.0 if the baseline average is zero (avoids division by zero).
    """
    if bucket_avg <= 0:
        return 0.0
    return current_volume / bucket_avg


def serialize_profile(profile: dict[str, float]) -> str:
    """Serialize a volume profile dict to JSON for Redis storage."""
    return json.dumps(profile)


def deserialize_profile(json_str: str) -> dict[str, float]:
    """Deserialize a volume profile from Redis JSON string."""
    return json.loads(json_str)
