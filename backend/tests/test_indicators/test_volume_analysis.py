"""Tests for volume analysis indicator."""

from app.indicators.volume_analysis import (
    compute_avg_volume,
    is_volume_breakout,
    volume_ratio,
)


class TestAvgVolume:
    def test_exact_period(self):
        volumes = [100] * 20
        assert compute_avg_volume(volumes, period=20) == 100

    def test_fewer_than_period(self):
        volumes = [100, 200]
        assert compute_avg_volume(volumes, period=20) == 150

    def test_empty(self):
        assert compute_avg_volume([], period=20) == 0

    def test_uses_last_n(self):
        volumes = [10] * 30 + [100] * 20
        assert compute_avg_volume(volumes, period=20) == 100


class TestVolumeBreakout:
    def test_breakout(self):
        assert is_volume_breakout(200, 100, threshold=1.5) is True

    def test_no_breakout(self):
        assert is_volume_breakout(100, 100, threshold=1.5) is False

    def test_exact_threshold(self):
        assert is_volume_breakout(150, 100, threshold=1.5) is True

    def test_zero_avg(self):
        assert is_volume_breakout(100, 0) is False


class TestVolumeRatio:
    def test_normal(self):
        assert volume_ratio(200, 100) == 2.0

    def test_zero_avg(self):
        assert volume_ratio(100, 0) == 0.0

    def test_below_avg(self):
        assert volume_ratio(50, 100) == 0.5
