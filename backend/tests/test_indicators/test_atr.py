"""Tests for ATR (Average True Range) indicator."""

import pytest

from app.indicators.atr import compute_atr
from app.indicators.candle_patterns import Candle


def _c(o: float, h: float, l: float, c: float, v: int = 100) -> Candle:
    return Candle(open=o, high=h, low=l, close=c, volume=v)


class TestComputeATR:
    def test_known_values(self):
        candles = [
            _c(100, 105, 98, 102),
            _c(102, 108, 100, 106),  # TR = max(8, |108-102|, |100-102|) = 8
            _c(106, 110, 103, 107),  # TR = max(7, |110-106|, |103-106|) = 7
            _c(107, 112, 105, 111),  # TR = max(7, |112-107|, |105-107|) = 7
        ]
        atr = compute_atr(candles, period=3)
        assert atr == pytest.approx((8 + 7 + 7) / 3)

    def test_insufficient_data_returns_zero(self):
        assert compute_atr([], period=14) == 0.0
        assert compute_atr([_c(100, 105, 98, 102)], period=14) == 0.0

    def test_flat_market(self):
        candles = [_c(100, 100, 100, 100) for _ in range(20)]
        assert compute_atr(candles, period=14) == 0.0

    def test_uses_last_n_true_ranges(self):
        candles = [
            _c(100, 120, 80, 100),   # anchor
            _c(100, 150, 50, 100),   # TR = max(100, |150-100|, |50-100|) = 100 (old, excluded with period=2)
            _c(100, 110, 90, 100),   # TR = max(20, 10, 10) = 20
            _c(100, 105, 95, 100),   # TR = max(10, 5, 5) = 10
        ]
        atr = compute_atr(candles, period=2)
        assert atr == pytest.approx((20 + 10) / 2)

    def test_gap_up_true_range(self):
        candles = [
            _c(100, 105, 98, 102),
            _c(110, 115, 108, 112),  # Gap up: TR = max(7, |115-102|, |108-102|) = 13
        ]
        atr = compute_atr(candles, period=14)
        assert atr == pytest.approx(13.0)

    def test_gap_down_true_range(self):
        candles = [
            _c(100, 105, 98, 102),
            _c(90, 95, 88, 92),  # Gap down: TR = max(7, |95-102|, |88-102|) = 14
        ]
        atr = compute_atr(candles, period=14)
        assert atr == pytest.approx(14.0)
