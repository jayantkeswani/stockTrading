"""Tests for previous day analysis indicator."""

from app.core.enums import DayBias
from app.indicators.previous_day import (
    PreviousDayLevels,
    analyze_previous_day,
    is_gap_down,
    is_gap_up,
)


# ---------------------------------------------------------------------------
# analyze_previous_day
# ---------------------------------------------------------------------------

class TestAnalyzePreviousDay:

    def test_bullish_bias(self):
        """Close > open AND close in upper 30% of range => BULLISH."""
        # Range: 90-110 = 20.  Upper 30% threshold = 90 + 0.7*20 = 104.
        # Close at 108 => close_position = (108-90)/20 = 0.9 >= 0.7, and 108 > 95
        result = analyze_previous_day(open_price=95.0, high=110.0, low=90.0, close=108.0)
        assert result.bias == DayBias.BULLISH

    def test_bearish_bias(self):
        """Close < open AND close in lower 30% of range => BEARISH."""
        # Range: 90-110 = 20.  Lower 30% threshold: close_position <= 0.3
        # Close at 93 => close_position = (93-90)/20 = 0.15 <= 0.3, and 93 < 105
        result = analyze_previous_day(open_price=105.0, high=110.0, low=90.0, close=93.0)
        assert result.bias == DayBias.BEARISH

    def test_neutral_close_mid_range(self):
        """Close in middle of range => NEUTRAL."""
        # Close at 100 => close_position = (100-90)/20 = 0.5, not >= 0.7 or <= 0.3
        result = analyze_previous_day(open_price=95.0, high=110.0, low=90.0, close=100.0)
        assert result.bias == DayBias.NEUTRAL

    def test_neutral_close_above_open_but_not_upper_30(self):
        """Close > open but not in upper 30% => NEUTRAL."""
        # close_position = (101-90)/20 = 0.55 < 0.7
        result = analyze_previous_day(open_price=100.0, high=110.0, low=90.0, close=101.0)
        assert result.bias == DayBias.NEUTRAL

    def test_neutral_close_below_open_but_not_lower_30(self):
        """Close < open but not in lower 30% => NEUTRAL."""
        # close_position = (99-90)/20 = 0.45 > 0.3
        result = analyze_previous_day(open_price=100.0, high=110.0, low=90.0, close=99.0)
        assert result.bias == DayBias.NEUTRAL

    def test_zero_range_returns_neutral(self):
        """All OHLC the same => zero range => NEUTRAL."""
        result = analyze_previous_day(open_price=100.0, high=100.0, low=100.0, close=100.0)
        assert result.bias == DayBias.NEUTRAL
        assert result.day_range == 0

    def test_level_values(self):
        """Verify that PDH, PDL, PDC, PDO, and day_range are correct."""
        result = analyze_previous_day(open_price=100.0, high=110.0, low=90.0, close=105.0)
        assert result.pdh == 110.0
        assert result.pdl == 90.0
        assert result.pdc == 105.0
        assert result.pdo == 100.0
        assert result.day_range == 20.0

    def test_bullish_boundary_close_position_exactly_0_7(self):
        """close_position == 0.7 with close > open => BULLISH."""
        # Range: 0-100. close_position = (70-0)/100 = 0.7
        result = analyze_previous_day(open_price=50.0, high=100.0, low=0.0, close=70.0)
        assert result.bias == DayBias.BULLISH

    def test_bearish_boundary_close_position_exactly_0_3(self):
        """close_position == 0.3 with close < open => BEARISH."""
        # Range: 0-100. close_position = (30-0)/100 = 0.3
        result = analyze_previous_day(open_price=50.0, high=100.0, low=0.0, close=30.0)
        assert result.bias == DayBias.BEARISH

    def test_returns_previous_day_levels_type(self):
        result = analyze_previous_day(open_price=100.0, high=110.0, low=90.0, close=105.0)
        assert isinstance(result, PreviousDayLevels)


# ---------------------------------------------------------------------------
# is_gap_up
# ---------------------------------------------------------------------------

class TestIsGapUp:

    def test_gap_up_above_threshold(self):
        """Current open 1% above PDC with 0.3% threshold => True."""
        assert is_gap_up(current_open=101.0, pdc=100.0, threshold_pct=0.3)

    def test_no_gap_up_below_threshold(self):
        """Current open only 0.1% above PDC => False."""
        assert not is_gap_up(current_open=100.1, pdc=100.0, threshold_pct=0.3)

    def test_gap_up_exactly_at_threshold(self):
        """Gap exactly at 0.3% => gap_pct = 0.3, which is NOT > 0.3 => False."""
        # pdc=100, open=100.3 => gap_pct = 0.3
        assert not is_gap_up(current_open=100.3, pdc=100.0, threshold_pct=0.3)

    def test_gap_up_pdc_zero(self):
        """PDC=0 guard."""
        assert not is_gap_up(current_open=100.0, pdc=0.0)

    def test_gap_down_scenario_returns_false(self):
        """Open below PDC should not register as gap up."""
        assert not is_gap_up(current_open=99.0, pdc=100.0)

    def test_custom_threshold(self):
        assert is_gap_up(current_open=101.0, pdc=100.0, threshold_pct=0.5)


# ---------------------------------------------------------------------------
# is_gap_down
# ---------------------------------------------------------------------------

class TestIsGapDown:

    def test_gap_down_above_threshold(self):
        """Current open 1% below PDC with 0.3% threshold => True."""
        assert is_gap_down(current_open=99.0, pdc=100.0, threshold_pct=0.3)

    def test_no_gap_down_below_threshold(self):
        assert not is_gap_down(current_open=99.9, pdc=100.0, threshold_pct=0.3)

    def test_gap_down_exactly_at_threshold(self):
        # pdc=100, open=99.7 => gap_pct = 0.3, NOT > 0.3
        assert not is_gap_down(current_open=99.7, pdc=100.0, threshold_pct=0.3)

    def test_gap_down_pdc_zero(self):
        assert not is_gap_down(current_open=99.0, pdc=0.0)

    def test_gap_up_scenario_returns_false(self):
        """Open above PDC should not register as gap down."""
        assert not is_gap_down(current_open=101.0, pdc=100.0)
