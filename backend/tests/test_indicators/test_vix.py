"""Tests for VIX processor."""

from app.indicators.vix import classify_vix, get_position_size_multiplier


# ---------------------------------------------------------------------------
# get_position_size_multiplier
# ---------------------------------------------------------------------------

class TestGetPositionSizeMultiplier:

    def test_low_vix_full_position(self):
        """VIX < 14 => 1.0."""
        assert get_position_size_multiplier(10.0) == 1.0
        assert get_position_size_multiplier(13.9) == 1.0

    def test_normal_vix_full_position(self):
        """VIX 14-17.9 => 1.0."""
        assert get_position_size_multiplier(14.0) == 1.0
        assert get_position_size_multiplier(17.9) == 1.0

    def test_high_vix_reduced_position(self):
        """VIX 18-21.9 => 0.7."""
        assert get_position_size_multiplier(18.0) == 0.7
        assert get_position_size_multiplier(21.9) == 0.7

    def test_extreme_vix_no_trade(self):
        """VIX >= 22 => 0.0."""
        assert get_position_size_multiplier(22.0) == 0.0
        assert get_position_size_multiplier(30.0) == 0.0

    def test_boundary_at_18(self):
        """Exactly VIX_HIGH threshold."""
        assert get_position_size_multiplier(18.0) == 0.7

    def test_boundary_at_22(self):
        """Exactly VIX_EXTREME threshold."""
        assert get_position_size_multiplier(22.0) == 0.0

    def test_zero_vix(self):
        assert get_position_size_multiplier(0.0) == 1.0


# ---------------------------------------------------------------------------
# classify_vix
# ---------------------------------------------------------------------------

class TestClassifyVix:

    def test_low(self):
        assert classify_vix(10.0) == "LOW"
        assert classify_vix(13.9) == "LOW"

    def test_normal(self):
        assert classify_vix(14.0) == "NORMAL"
        assert classify_vix(17.9) == "NORMAL"

    def test_high(self):
        assert classify_vix(18.0) == "HIGH"
        assert classify_vix(21.9) == "HIGH"

    def test_extreme(self):
        assert classify_vix(22.0) == "EXTREME"
        assert classify_vix(50.0) == "EXTREME"

    def test_boundary_14(self):
        """VIX exactly 14: not < 14 => not LOW => NORMAL."""
        assert classify_vix(14.0) == "NORMAL"

    def test_boundary_18(self):
        assert classify_vix(18.0) == "HIGH"

    def test_boundary_22(self):
        assert classify_vix(22.0) == "EXTREME"
