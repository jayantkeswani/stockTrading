"""Tests for Open Interest analysis indicator."""

from app.indicators.open_interest import (
    OIAnalysis,
    _calculate_max_pain,
    analyze_option_chain,
    is_oi_supporting_direction,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_strikes():
    """Simple 3-strike option chain."""
    return [
        {"strike_price": 100, "ce_oi": 5000, "pe_oi": 3000},
        {"strike_price": 105, "ce_oi": 8000, "pe_oi": 2000},
        {"strike_price": 110, "ce_oi": 3000, "pe_oi": 10000},
    ]


# ---------------------------------------------------------------------------
# analyze_option_chain
# ---------------------------------------------------------------------------

class TestAnalyzeOptionChain:

    def test_empty_strikes_returns_none(self):
        assert analyze_option_chain([]) is None

    def test_pcr_calculation(self):
        """PCR = total_pe_oi / total_ce_oi."""
        strikes = _make_strikes()
        result = analyze_option_chain(strikes)
        assert result is not None
        # total_ce = 5000+8000+3000 = 16000, total_pe = 3000+2000+10000 = 15000
        expected_pcr = 15000 / 16000
        assert abs(result.pcr - expected_pcr) < 1e-9

    def test_max_ce_oi_strike(self):
        """Strike with highest CE OI = 105 (8000)."""
        result = analyze_option_chain(_make_strikes())
        assert result is not None
        assert result.max_ce_oi_strike == 105

    def test_max_pe_oi_strike(self):
        """Strike with highest PE OI = 110 (10000)."""
        result = analyze_option_chain(_make_strikes())
        assert result is not None
        assert result.max_pe_oi_strike == 110

    def test_sentiment_bullish(self):
        """PCR < 0.7 => BULLISH."""
        strikes = [
            {"strike_price": 100, "ce_oi": 10000, "pe_oi": 2000},
            {"strike_price": 105, "ce_oi": 10000, "pe_oi": 2000},
        ]
        result = analyze_option_chain(strikes)
        assert result is not None
        # pcr = 4000/20000 = 0.2
        assert result.sentiment == "BULLISH"

    def test_sentiment_bearish(self):
        """PCR > 1.5 => BEARISH."""
        strikes = [
            {"strike_price": 100, "ce_oi": 1000, "pe_oi": 5000},
            {"strike_price": 105, "ce_oi": 1000, "pe_oi": 5000},
        ]
        result = analyze_option_chain(strikes)
        assert result is not None
        # pcr = 10000/2000 = 5.0
        assert result.sentiment == "BEARISH"

    def test_sentiment_neutral(self):
        """0.7 <= PCR <= 1.5 => NEUTRAL."""
        strikes = [
            {"strike_price": 100, "ce_oi": 10000, "pe_oi": 10000},
        ]
        result = analyze_option_chain(strikes)
        assert result is not None
        # pcr = 1.0
        assert result.sentiment == "NEUTRAL"

    def test_pcr_zero_ce_oi(self):
        """All CE OI = 0 => PCR = 0 (guard against division by zero)."""
        strikes = [
            {"strike_price": 100, "ce_oi": 0, "pe_oi": 5000},
        ]
        result = analyze_option_chain(strikes)
        assert result is not None
        assert result.pcr == 0

    def test_total_oi_values(self):
        result = analyze_option_chain(_make_strikes())
        assert result is not None
        assert result.total_ce_oi == 16000
        assert result.total_pe_oi == 15000

    def test_returns_oi_analysis_type(self):
        result = analyze_option_chain(_make_strikes())
        assert isinstance(result, OIAnalysis)

    def test_missing_oi_keys_default_to_zero(self):
        """Strikes without ce_oi/pe_oi should use 0."""
        strikes = [
            {"strike_price": 100},
            {"strike_price": 105, "ce_oi": 1000},
        ]
        result = analyze_option_chain(strikes)
        assert result is not None
        assert result.total_ce_oi == 1000
        assert result.total_pe_oi == 0


# ---------------------------------------------------------------------------
# _calculate_max_pain
# ---------------------------------------------------------------------------

class TestCalculateMaxPain:

    def test_simple_max_pain(self):
        """Max pain is the strike where total buyer losses are minimized.

        Strikes: 100, 105, 110
        For target=100:
          CE losses: max(0,100-100)*5000 + max(0,100-105)*8000 + max(0,100-110)*3000 = 0
          PE losses: max(0,100-100)*3000 + max(0,105-100)*2000 + max(0,110-100)*10000 = 0+10000+100000 = 110000
          Total = 110000
        For target=105:
          CE losses: max(0,105-100)*5000 + 0 + 0 = 25000
          PE losses: 0 + 0 + max(0,110-105)*10000 = 50000
          Total = 75000
        For target=110:
          CE losses: max(0,110-100)*5000 + max(0,110-105)*8000 + 0 = 50000+40000 = 90000
          PE losses: 0
          Total = 90000
        Min = 75000 at strike 105
        """
        strikes = _make_strikes()
        assert _calculate_max_pain(strikes) == 105

    def test_single_strike(self):
        strikes = [{"strike_price": 100, "ce_oi": 1000, "pe_oi": 2000}]
        assert _calculate_max_pain(strikes) == 100

    def test_max_pain_returned_by_analyze(self):
        result = analyze_option_chain(_make_strikes())
        assert result is not None
        assert result.max_pain == 105


# ---------------------------------------------------------------------------
# is_oi_supporting_direction
# ---------------------------------------------------------------------------

class TestIsOISupportingDirection:

    def test_call_supported(self):
        """For CALL: price > max PE OI strike => True."""
        assert is_oi_supporting_direction(
            current_price=112.0,
            max_pe_oi_strike=110.0,
            max_ce_oi_strike=120.0,
            direction="CALL",
        )

    def test_call_not_supported(self):
        """For CALL: price <= max PE OI strike => False."""
        assert not is_oi_supporting_direction(
            current_price=108.0,
            max_pe_oi_strike=110.0,
            max_ce_oi_strike=120.0,
            direction="CALL",
        )

    def test_put_supported(self):
        """For PUT: price < max CE OI strike => True."""
        assert is_oi_supporting_direction(
            current_price=115.0,
            max_pe_oi_strike=100.0,
            max_ce_oi_strike=120.0,
            direction="PUT",
        )

    def test_put_not_supported(self):
        """For PUT: price >= max CE OI strike => False."""
        assert not is_oi_supporting_direction(
            current_price=125.0,
            max_pe_oi_strike=100.0,
            max_ce_oi_strike=120.0,
            direction="PUT",
        )

    def test_call_exactly_at_pe_strike(self):
        """Price == max PE OI strike => not > => False."""
        assert not is_oi_supporting_direction(
            current_price=110.0,
            max_pe_oi_strike=110.0,
            max_ce_oi_strike=120.0,
            direction="CALL",
        )

    def test_put_exactly_at_ce_strike(self):
        """Price == max CE OI strike => not < => False."""
        assert not is_oi_supporting_direction(
            current_price=120.0,
            max_pe_oi_strike=100.0,
            max_ce_oi_strike=120.0,
            direction="PUT",
        )

    def test_invalid_direction_returns_false(self):
        assert not is_oi_supporting_direction(
            current_price=110.0,
            max_pe_oi_strike=100.0,
            max_ce_oi_strike=120.0,
            direction="STRADDLE",
        )
