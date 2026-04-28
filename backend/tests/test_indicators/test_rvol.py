"""Tests for RVOL (Relative Volume) indicator."""

from datetime import date

from app.indicators.candle_patterns import Candle
from app.indicators.rvol import (
    build_volume_profile,
    compute_rvol,
    deserialize_profile,
    serialize_profile,
)


def _candle(volume: int) -> Candle:
    return Candle(open=100, high=101, low=99, close=100, volume=volume)


class TestBuildVolumeProfile:
    def test_single_day(self):
        data = {
            date(2026, 4, 1): [_candle(1000), _candle(2000), _candle(3000)],
        }
        profile = build_volume_profile(data)
        assert profile["0"] == 1000.0
        assert profile["1"] == 2000.0
        assert profile["2"] == 3000.0

    def test_multiple_days_averages(self):
        data = {
            date(2026, 4, 1): [_candle(1000), _candle(2000)],
            date(2026, 4, 2): [_candle(3000), _candle(4000)],
        }
        profile = build_volume_profile(data)
        assert profile["0"] == 2000.0  # avg(1000, 3000)
        assert profile["1"] == 3000.0  # avg(2000, 4000)

    def test_empty_data(self):
        profile = build_volume_profile({})
        assert profile == {}

    def test_uneven_days(self):
        data = {
            date(2026, 4, 1): [_candle(1000), _candle(2000), _candle(3000)],
            date(2026, 4, 2): [_candle(5000)],
        }
        profile = build_volume_profile(data)
        assert profile["0"] == 3000.0  # avg(1000, 5000)
        assert profile["1"] == 2000.0  # only day 1
        assert profile["2"] == 3000.0  # only day 1

    def test_twenty_days(self):
        data = {}
        for i in range(20):
            data[date(2026, 4, 1 + i)] = [_candle(100 * (i + 1))]
        profile = build_volume_profile(data)
        expected_avg = sum(100 * (i + 1) for i in range(20)) / 20
        assert abs(profile["0"] - expected_avg) < 0.01


class TestComputeRVOL:
    def test_above_average(self):
        assert compute_rvol(3000, 1000.0) == 3.0

    def test_at_average(self):
        assert compute_rvol(1000, 1000.0) == 1.0

    def test_below_average(self):
        assert compute_rvol(500, 1000.0) == 0.5

    def test_zero_baseline(self):
        assert compute_rvol(1000, 0.0) == 0.0

    def test_negative_baseline(self):
        assert compute_rvol(1000, -100.0) == 0.0

    def test_zero_current(self):
        assert compute_rvol(0, 1000.0) == 0.0


class TestSerialization:
    def test_roundtrip(self):
        profile = {"0": 1000.5, "1": 2000.3, "74": 500.0}
        serialized = serialize_profile(profile)
        deserialized = deserialize_profile(serialized)
        assert deserialized == profile

    def test_empty_profile(self):
        profile = {}
        assert deserialize_profile(serialize_profile(profile)) == {}
