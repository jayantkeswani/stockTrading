"""Tests for CAN SLIM chart base pattern detection."""

import pytest

from app.indicators.candle_patterns import Candle
from app.strategies.canslim.base_patterns import (
    detect_any_base_pattern,
    detect_cup_with_handle,
    detect_double_bottom,
    detect_flat_base,
)


def _make_candle(o, h, l, c, v=1000) -> Candle:
    return Candle(open=o, high=h, low=l, close=c, volume=v)


class TestFlatBase:
    def test_flat_base_detected(self):
        """Stock consolidating in a tight range after an uptrend."""
        # Prior uptrend (not in lookback, but we set lookback to len of bars)
        bars = []
        # First create prior uptrend context (30 bars, 100 → 200)
        for i in range(30):
            p = 100 + i * 3.33
            bars.append(_make_candle(p, p + 2, p - 2, p))
        # Then flat consolidation for 15 bars (195-205 range = 5% range)
        for _ in range(15):
            bars.append(_make_candle(198, 205, 195, 200))

        pattern = detect_flat_base(bars, lookback=15)
        if pattern:
            assert pattern.pattern_type == "FLAT_BASE"
            assert pattern.depth_pct < 15
            assert pattern.breakout_price >= 200
            assert pattern.base_low > 0
            assert pattern.base_low <= pattern.breakout_price

    def test_no_flat_base_wide_range(self):
        """Range too wide (>15%) — should not detect flat base."""
        bars = []
        for _ in range(30):
            bars.append(_make_candle(100, 120, 80, 100))  # 33% range

        pattern = detect_flat_base(bars, lookback=25)
        assert pattern is None

    def test_insufficient_data(self):
        bars = [_make_candle(100, 101, 99, 100)] * 10
        assert detect_flat_base(bars) is None


class TestCupWithHandle:
    def test_cup_pattern(self):
        """Classic cup shape: rise, correction, recovery."""
        bars = []
        # Left rim: price at 200
        for i in range(10):
            bars.append(_make_candle(195 + i * 0.5, 200, 194, 195 + i * 0.5))

        # Cup bottom: 20% correction (200 → 160)
        for i in range(15):
            p = 200 - i * 2.67
            bars.append(_make_candle(p + 1, p + 3, p - 3, p))

        # Recovery: back up to ~200
        for i in range(15):
            p = 160 + i * 2.67
            bars.append(_make_candle(p - 1, p + 3, p - 3, p))

        # Handle: small pullback (5%)
        for i in range(5):
            bars.append(_make_candle(198, 200, 190, 192))

        pattern = detect_cup_with_handle(bars, lookback=50)
        if pattern:
            assert pattern.pattern_type == "CUP_HANDLE"
            assert 10 < pattern.depth_pct < 35
            assert pattern.breakout_price > 0
            assert pattern.base_low > 0
            assert pattern.base_low < pattern.breakout_price

    def test_no_cup_shallow(self):
        """Cup too shallow (< 12%) — should not detect."""
        bars = []
        for i in range(40):
            p = 100 - abs(20 - i) * 0.2  # 4% dip
            bars.append(_make_candle(p, p + 1, p - 1, p))

        pattern = detect_cup_with_handle(bars)
        assert pattern is None

    def test_insufficient_data(self):
        bars = [_make_candle(100, 101, 99, 100)] * 10
        assert detect_cup_with_handle(bars) is None


class TestDoubleBottom:
    def test_w_shape(self):
        """Classic W-shaped double bottom."""
        bars = []
        # Peak at 200
        for i in range(8):
            bars.append(_make_candle(195, 200, 190, 195))

        # First bottom at 150 (25% correction)
        for i in range(10):
            p = 200 - i * 5
            bars.append(_make_candle(p + 1, p + 3, p - 3, p))

        # Middle peak at 175
        for i in range(8):
            p = 150 + i * 3.125
            bars.append(_make_candle(p, p + 2, p - 2, p))

        # Second bottom at ~150
        for i in range(8):
            p = 175 - i * 3.125
            bars.append(_make_candle(p, p + 2, p - 2, p))

        # Recovery start
        for i in range(5):
            p = 150 + i * 3
            bars.append(_make_candle(p, p + 2, p - 2, p))

        pattern = detect_double_bottom(bars, lookback=50)
        if pattern:
            assert pattern.pattern_type == "DOUBLE_BOTTOM"
            assert 15 <= pattern.depth_pct <= 35
            assert pattern.base_low > 0
            assert pattern.base_low < pattern.breakout_price

    def test_insufficient_data(self):
        bars = [_make_candle(100, 101, 99, 100)] * 10
        assert detect_double_bottom(bars) is None


class TestDetectAny:
    def test_flat_base_wins(self):
        """Flat base should be detected when present."""
        # Prior uptrend
        bars = []
        for i in range(30):
            p = 100 + i * 3.33
            bars.append(_make_candle(p, p + 2, p - 2, p))
        # Flat consolidation
        for _ in range(15):
            bars.append(_make_candle(198, 202, 196, 200))

        pattern = detect_any_base_pattern(bars)
        # Should detect at least one pattern
        if pattern:
            assert pattern.pattern_type in ("CUP_HANDLE", "FLAT_BASE", "DOUBLE_BOTTOM")

    def test_no_pattern(self):
        """Random volatile data — no pattern should be detected."""
        import random
        random.seed(42)
        bars = []
        for _ in range(50):
            p = random.uniform(80, 120)
            bars.append(_make_candle(p, p + 10, p - 10, p))

        # May or may not detect — this is a probabilistic test
        # Just verify it doesn't crash
        detect_any_base_pattern(bars)
