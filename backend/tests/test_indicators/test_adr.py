"""Tests for ADR (Average Daily Range) indicator."""

from app.indicators.adr import adr_qualifies, compute_adr
from app.indicators.candle_patterns import Candle


def _make_candle(open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(open=open_, high=high, low=low, close=close, volume=100_000)


class TestComputeADR:
    def test_basic_computation(self):
        candles = [
            _make_candle(100, 103, 97, 101),  # range=6, 6/101 = 5.94%
            _make_candle(101, 104, 99, 102),  # range=5, 5/102 = 4.90%
        ]
        adr = compute_adr(candles, period=20)
        assert 5.0 < adr < 5.5

    def test_respects_period(self):
        candles = [
            _make_candle(100, 110, 90, 100),  # range=20, 20%
            _make_candle(100, 110, 90, 100),  # range=20, 20%
            _make_candle(100, 101, 99, 100),  # range=2, 2%
        ]
        adr_last_1 = compute_adr(candles, period=1)
        adr_all = compute_adr(candles, period=20)
        assert adr_last_1 < 3.0
        assert adr_all > 10.0

    def test_empty_candles(self):
        assert compute_adr([], period=20) == 0.0

    def test_zero_close_skipped(self):
        candles = [
            _make_candle(100, 103, 97, 0),
            _make_candle(100, 103, 97, 101),
        ]
        adr = compute_adr(candles, period=20)
        assert adr > 0

    def test_all_zero_close(self):
        candles = [_make_candle(100, 103, 97, 0) for _ in range(5)]
        assert compute_adr(candles) == 0.0

    def test_zero_range_days(self):
        candles = [_make_candle(100, 100, 100, 100) for _ in range(5)]
        assert compute_adr(candles) == 0.0


class TestADRQualifies:
    def test_above_threshold(self):
        assert adr_qualifies(2.5, min_adr=1.5) is True

    def test_at_threshold(self):
        assert adr_qualifies(1.5, min_adr=1.5) is True

    def test_below_threshold(self):
        assert adr_qualifies(1.0, min_adr=1.5) is False

    def test_custom_threshold(self):
        assert adr_qualifies(2.0, min_adr=2.5) is False
