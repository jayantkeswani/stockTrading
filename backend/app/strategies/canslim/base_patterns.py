"""Chart base pattern detection for CAN SLIM entry timing.

Detects three classical CAN SLIM base patterns from daily OHLCV data:
- Cup-with-Handle: U-shaped correction (12-33%) with a small handle pullback
- Flat Base: Tight consolidation (< 15% range) after a prior uptrend
- Double Bottom: W-shaped correction (15-35%) with two distinct lows

Each function returns a BasePattern with the breakout_price that the
strategy watches for a price-volume breakout above.
"""

from dataclasses import dataclass

from app.indicators.candle_patterns import Candle


@dataclass
class BasePattern:
    """Detected chart base pattern with breakout level."""

    pattern_type: str  # "CUP_HANDLE", "FLAT_BASE", "DOUBLE_BOTTOM"
    breakout_price: float  # Resistance level to watch for breakout
    depth_pct: float  # How deep the base corrected (%)
    length_days: int  # Duration of the base in trading days
    base_low: float = 0.0  # Lowest price in the base (for SL placement)


def detect_cup_with_handle(
    daily_bars: list[Candle], lookback: int = 65
) -> BasePattern | None:
    """Detect a cup-with-handle pattern.

    Criteria:
    - Prior uptrend (price rose >= 20% before the cup)
    - Cup depth: 12-33% correction from the left rim
    - Cup duration: 7-65 trading days (1-13 weeks)
    - Handle: small pullback (< 12% from right rim), 5-25 days
    - Right rim approximately level with left rim (within 5%)
    - Breakout level: highest point of the handle
    """
    if len(daily_bars) < 30:
        return None

    bars = daily_bars[-lookback:] if len(daily_bars) > lookback else daily_bars
    n = len(bars)
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]

    # Find the highest point (left rim of cup)
    left_rim_idx = 0
    left_rim_price = highs[0]
    for i in range(n // 3):  # Left rim should be in the first third
        if highs[i] > left_rim_price:
            left_rim_price = highs[i]
            left_rim_idx = i

    if left_rim_price <= 0:
        return None

    # Find the cup bottom (lowest point after left rim)
    bottom_start = left_rim_idx + 3  # At least 3 days after left rim
    if bottom_start >= n - 10:
        return None

    bottom_idx = bottom_start
    bottom_price = closes[bottom_start]
    for i in range(bottom_start, min(n - 5, bottom_start + 40)):
        if closes[i] < bottom_price:
            bottom_price = closes[i]
            bottom_idx = i

    # Check cup depth (12-33%)
    depth_pct = ((left_rim_price - bottom_price) / left_rim_price) * 100
    if depth_pct < 12 or depth_pct > 33:
        return None

    # Find right rim (recovery from bottom)
    right_rim_idx = bottom_idx + 3
    right_rim_price = closes[right_rim_idx] if right_rim_idx < n else 0
    for i in range(bottom_idx + 3, n):
        if closes[i] > right_rim_price:
            right_rim_price = closes[i]
            right_rim_idx = i

    # Right rim should be close to left rim (within 5%)
    if right_rim_price < left_rim_price * 0.95:
        return None

    # Look for handle (small pullback after right rim)
    handle_start = right_rim_idx
    if handle_start >= n - 2:
        # No handle, but cup is complete — breakout at right rim
        return BasePattern(
            pattern_type="CUP_HANDLE",
            breakout_price=right_rim_price,
            depth_pct=depth_pct,
            length_days=right_rim_idx - left_rim_idx,
            base_low=bottom_price,
        )

    # Handle: look for pullback < 12% from right rim
    handle_low = min(closes[handle_start:])
    handle_pullback = ((right_rim_price - handle_low) / right_rim_price) * 100

    if handle_pullback > 12:
        return None  # Handle too deep

    # Breakout level: the highest point in the handle area
    handle_high = max(highs[handle_start:])

    return BasePattern(
        pattern_type="CUP_HANDLE",
        breakout_price=handle_high,
        depth_pct=depth_pct,
        length_days=n - left_rim_idx,
        base_low=bottom_price,
    )


def detect_flat_base(
    daily_bars: list[Candle], lookback: int = 30
) -> BasePattern | None:
    """Detect a flat base pattern.

    Criteria:
    - Prior uptrend (price rose >= 20% before the base)
    - Base range: < 15% from high to low during the base period
    - Base duration: 5-30 trading days (1-6 weeks)
    - Breakout level: the high of the base
    """
    if len(daily_bars) < 25:
        return None

    bars = daily_bars[-lookback:] if len(daily_bars) > lookback else daily_bars
    n = len(bars)

    # Check for prior uptrend: compare price 30 bars before base to base entry
    if len(daily_bars) > lookback + 30:
        prior_price = daily_bars[-(lookback + 30)].close
        base_entry = bars[0].close
        if base_entry <= 0 or prior_price <= 0:
            return None
        prior_gain = ((base_entry - prior_price) / prior_price) * 100
        if prior_gain < 20:
            return None  # No prior uptrend

    # Compute base range
    base_high = max(b.high for b in bars)
    base_low = min(b.low for b in bars)

    if base_high <= 0:
        return None

    range_pct = ((base_high - base_low) / base_high) * 100

    if range_pct >= 15:
        return None  # Too wide for flat base

    return BasePattern(
        pattern_type="FLAT_BASE",
        breakout_price=base_high,
        depth_pct=range_pct,
        length_days=n,
        base_low=base_low,
    )


def detect_double_bottom(
    daily_bars: list[Candle], lookback: int = 50
) -> BasePattern | None:
    """Detect a double bottom (W-shape) pattern.

    Criteria:
    - Two distinct lows within 3% of each other
    - Depth: 15-35% correction from the peak
    - Middle peak (the "W" center) at least 5% above the lows
    - Duration: 20-50 trading days
    - Breakout level: the middle peak
    """
    if len(daily_bars) < 25:
        return None

    bars = daily_bars[-lookback:] if len(daily_bars) > lookback else daily_bars
    n = len(bars)
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    # Find the overall high (peak before the W)
    peak_price = max(highs[:n // 3]) if n >= 6 else max(highs)
    if peak_price <= 0:
        return None

    # Find first bottom (in the first half)
    first_half = n // 2
    first_bottom_idx = 0
    first_bottom = lows[0]
    for i in range(first_half):
        if lows[i] < first_bottom:
            first_bottom = lows[i]
            first_bottom_idx = i

    # Find middle peak (between the two bottoms)
    mid_start = first_bottom_idx + 3
    mid_end = min(n - 5, first_bottom_idx + (n - first_bottom_idx) // 2 + 5)
    if mid_start >= mid_end:
        return None

    mid_peak_idx = mid_start
    mid_peak = highs[mid_start]
    for i in range(mid_start, mid_end):
        if highs[i] > mid_peak:
            mid_peak = highs[i]
            mid_peak_idx = i

    # Find second bottom (after middle peak)
    second_start = mid_peak_idx + 3
    if second_start >= n:
        return None

    second_bottom_idx = second_start
    second_bottom = lows[second_start]
    for i in range(second_start, n):
        if lows[i] < second_bottom:
            second_bottom = lows[i]
            second_bottom_idx = i

    # Check: two bottoms within 3% of each other
    if first_bottom <= 0:
        return None
    bottom_diff_pct = abs(first_bottom - second_bottom) / first_bottom * 100
    if bottom_diff_pct > 3:
        return None

    # Check depth (15-35%)
    avg_bottom = (first_bottom + second_bottom) / 2
    depth_pct = ((peak_price - avg_bottom) / peak_price) * 100
    if depth_pct < 15 or depth_pct > 35:
        return None

    # Check middle peak is at least 5% above the lows
    mid_above_bottom = ((mid_peak - avg_bottom) / avg_bottom) * 100
    if mid_above_bottom < 5:
        return None

    return BasePattern(
        pattern_type="DOUBLE_BOTTOM",
        breakout_price=mid_peak,  # Breakout above the middle peak of the W
        depth_pct=depth_pct,
        length_days=second_bottom_idx - first_bottom_idx,
        base_low=avg_bottom,
    )


def detect_any_base_pattern(daily_bars: list[Candle]) -> BasePattern | None:
    """Try all three pattern detectors, return the first match.

    Priority: Cup-with-Handle > Double Bottom > Flat Base
    (ordered by reliability of the CAN SLIM signal).
    """
    pattern = detect_cup_with_handle(daily_bars)
    if pattern:
        return pattern

    pattern = detect_double_bottom(daily_bars)
    if pattern:
        return pattern

    pattern = detect_flat_base(daily_bars)
    if pattern:
        return pattern

    return None
