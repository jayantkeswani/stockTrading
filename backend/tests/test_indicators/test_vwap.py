"""Comprehensive tests for VWAP indicator."""

import numpy as np

from app.indicators.vwap import (
    VWAPResult,
    calculate_vwap,
    is_pullback_to_vwap,
    price_distance_from_vwap,
)


# ---------------------------------------------------------------------------
# calculate_vwap — hand-verified values
# ---------------------------------------------------------------------------

class TestCalculateVwap:

    def test_basic_known_values(self):
        """Hand-calculate VWAP for two candles and compare."""
        highs = [102.0, 104.0]
        lows = [98.0, 100.0]
        closes = [100.0, 102.0]
        volumes = [1000, 2000]

        # Typical prices: (102+98+100)/3 = 100.0,  (104+100+102)/3 = 102.0
        # cum_tp_vol:  100*1000 = 100_000,  100_000 + 102*2000 = 304_000
        # cum_vol:     1000, 3000
        # vwap[-1] = 304_000 / 3000 = 101.333...
        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        expected_vwap = 304_000 / 3000
        assert abs(result.vwap - expected_vwap) < 1e-9

    def test_bands_surround_vwap(self):
        highs = [100.5, 101.0, 101.5, 102.0, 101.8]
        lows = [99.5, 100.0, 100.5, 101.0, 100.8]
        closes = [100.0, 100.5, 101.0, 101.5, 101.2]
        volumes = [1000, 1500, 1200, 1800, 1100]

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        assert result.upper_band > result.vwap
        assert result.lower_band < result.vwap

    def test_ascending_prices(self):
        """Monotonically rising prices — VWAP should lag below last typical price."""
        highs = [101, 102, 103, 104, 105]
        lows = [99, 100, 101, 102, 103]
        closes = [100, 101, 102, 103, 104]
        volumes = [100, 100, 100, 100, 100]

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        last_tp = (105 + 103 + 104) / 3
        assert result.vwap < last_tp  # VWAP lags behind ascending prices

    def test_descending_prices(self):
        """Monotonically falling prices — VWAP should lag above last typical price."""
        highs = [105, 104, 103, 102, 101]
        lows = [103, 102, 101, 100, 99]
        closes = [104, 103, 102, 101, 100]
        volumes = [100, 100, 100, 100, 100]

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        last_tp = (101 + 99 + 100) / 3
        assert result.vwap > last_tp  # VWAP lags above descending prices

    def test_flat_prices(self):
        """All candles identical — VWAP = typical price, bands collapse to near-zero."""
        highs = [100.0] * 5
        lows = [100.0] * 5
        closes = [100.0] * 5
        volumes = [1000] * 5

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        assert abs(result.vwap - 100.0) < 1e-9
        # With identical candles, std dev is 0 => bands == vwap
        assert abs(result.upper_band - result.vwap) < 1e-9
        assert abs(result.lower_band - result.vwap) < 1e-9

    def test_insufficient_data_single_candle(self):
        result = calculate_vwap([100], [99], [100], [1000])
        assert result is None

    def test_insufficient_data_empty(self):
        result = calculate_vwap([], [], [], [])
        assert result is None

    def test_zero_total_volume(self):
        """All volumes zero should return None."""
        result = calculate_vwap([100, 101], [99, 100], [100, 101], [0, 0])
        assert result is None

    def test_mixed_zero_volume(self):
        """Some candles have zero volume — should still compute."""
        highs = [102.0, 104.0, 103.0]
        lows = [98.0, 100.0, 101.0]
        closes = [100.0, 102.0, 102.0]
        volumes = [1000, 0, 2000]

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        # Total volume > 0, should work
        assert result.vwap > 0

    def test_result_is_vwap_result_type(self):
        result = calculate_vwap(
            [100, 101], [99, 100], [100, 101], [500, 500]
        )
        assert isinstance(result, VWAPResult)

    def test_large_volume_candle_dominates(self):
        """A single candle with huge volume should pull VWAP toward its typical price."""
        highs = [100, 200]
        lows = [100, 200]
        closes = [100, 200]
        volumes = [1, 1_000_000]

        result = calculate_vwap(highs, lows, closes, volumes)
        assert result is not None
        # VWAP should be very close to 200
        assert result.vwap > 199.9


# ---------------------------------------------------------------------------
# price_distance_from_vwap
# ---------------------------------------------------------------------------

class TestPriceDistanceFromVwap:

    def test_zero_distance(self):
        assert price_distance_from_vwap(100.0, 100.0) == 0.0

    def test_positive_distance(self):
        """Price above VWAP -> positive %."""
        d = price_distance_from_vwap(101.0, 100.0)
        assert abs(d - 1.0) < 1e-9

    def test_negative_distance(self):
        """Price below VWAP -> negative %."""
        d = price_distance_from_vwap(99.0, 100.0)
        assert abs(d - (-1.0)) < 1e-9

    def test_vwap_zero_returns_zero(self):
        """Division by zero guard."""
        assert price_distance_from_vwap(105.0, 0.0) == 0.0

    def test_large_distance(self):
        d = price_distance_from_vwap(200.0, 100.0)
        assert abs(d - 100.0) < 1e-9


# ---------------------------------------------------------------------------
# is_pullback_to_vwap
# ---------------------------------------------------------------------------

class TestIsPullbackToVwap:

    def test_within_proximity(self):
        # 0.1% away from VWAP
        assert is_pullback_to_vwap(100.1, 100.0, proximity_pct=0.15)

    def test_outside_proximity(self):
        # 2% away from VWAP
        assert not is_pullback_to_vwap(102.0, 100.0, proximity_pct=0.15)

    def test_exactly_at_boundary(self):
        """Price just inside the proximity threshold (float-safe)."""
        # 100.14 is 0.14% from 100 — safely within 0.15%
        assert is_pullback_to_vwap(100.14, 100.0, proximity_pct=0.15)

    def test_just_outside_boundary(self):
        assert not is_pullback_to_vwap(100.16, 100.0, proximity_pct=0.15)

    def test_price_below_vwap_within_proximity(self):
        """Works symmetrically for prices below VWAP."""
        assert is_pullback_to_vwap(99.9, 100.0, proximity_pct=0.15)

    def test_price_equals_vwap(self):
        assert is_pullback_to_vwap(100.0, 100.0)

    def test_custom_proximity(self):
        # With 1% proximity, 100.5 is 0.5% from 100 => within
        assert is_pullback_to_vwap(100.5, 100.0, proximity_pct=1.0)
        # 101.5 is 1.5% from 100 => outside
        assert not is_pullback_to_vwap(101.5, 100.0, proximity_pct=1.0)
