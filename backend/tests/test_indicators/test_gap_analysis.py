"""Tests for gap analysis indicator."""

import pytest

from app.indicators.candle_patterns import Candle
from app.indicators.gap_analysis import detect_gap, is_gap_continuation


def _c(o: float, h: float, l: float, c: float, v: int = 100) -> Candle:
    return Candle(open=o, high=h, low=l, close=c, volume=v)


class TestDetectGap:
    def test_gap_up(self):
        result = detect_gap(today_open=1010, prev_close=1000, min_gap_pct=0.5)
        assert result is not None
        assert result["direction"] == "UP"
        assert result["gap_pct"] == pytest.approx(1.0)
        assert result["gap_level"] == 1000

    def test_gap_down(self):
        result = detect_gap(today_open=990, prev_close=1000, min_gap_pct=0.5)
        assert result is not None
        assert result["direction"] == "DOWN"
        assert result["gap_pct"] == pytest.approx(1.0)
        assert result["gap_level"] == 1000

    def test_no_gap_below_threshold(self):
        result = detect_gap(today_open=1003, prev_close=1000, min_gap_pct=0.5)
        assert result is None

    def test_exact_threshold(self):
        result = detect_gap(today_open=1005, prev_close=1000, min_gap_pct=0.5)
        assert result is not None

    def test_zero_prev_close(self):
        assert detect_gap(today_open=100, prev_close=0) is None

    def test_custom_threshold(self):
        result = detect_gap(today_open=1002, prev_close=1000, min_gap_pct=0.1)
        assert result is not None
        assert result["gap_pct"] == pytest.approx(0.2)


class TestIsGapContinuation:
    def test_gap_up_holds(self):
        gap_level = 1000
        candles = [
            _c(1010, 1015, 1008, 1012),  # formation
            _c(1012, 1018, 1010, 1015),  # formation
            _c(1015, 1020, 1013, 1018),  # formation
            _c(1018, 1025, 1016, 1022),  # confirmation — holds above 1000
        ]
        assert is_gap_continuation(candles, "UP", gap_level) is True

    def test_gap_up_fills(self):
        gap_level = 1000
        candles = [
            _c(1010, 1015, 1008, 1012),
            _c(1012, 1018, 1010, 1015),
            _c(1015, 1020, 1013, 1018),
            _c(1018, 1020, 995, 998),  # close below gap level
        ]
        assert is_gap_continuation(candles, "UP", gap_level) is False

    def test_gap_down_holds(self):
        gap_level = 1000
        candles = [
            _c(990, 995, 985, 988),
            _c(988, 992, 983, 985),
            _c(985, 990, 980, 982),
            _c(982, 988, 978, 980),  # holds below 1000
        ]
        assert is_gap_continuation(candles, "DOWN", gap_level) is True

    def test_gap_down_fills(self):
        gap_level = 1000
        candles = [
            _c(990, 995, 985, 988),
            _c(988, 992, 983, 985),
            _c(985, 990, 980, 982),
            _c(982, 1005, 978, 1002),  # close above gap level
        ]
        assert is_gap_continuation(candles, "DOWN", gap_level) is False

    def test_insufficient_candles(self):
        assert is_gap_continuation([_c(100, 105, 98, 102)] * 3, "UP", 90) is False

    def test_multiple_confirmation_candles(self):
        gap_level = 1000
        candles = [
            _c(1010, 1015, 1008, 1012),
            _c(1012, 1018, 1010, 1015),
            _c(1015, 1020, 1013, 1018),
            _c(1018, 1025, 1016, 1022),
            _c(1022, 1030, 1020, 1028),
            _c(1028, 1035, 1025, 1032),
        ]
        assert is_gap_continuation(candles, "UP", gap_level) is True
