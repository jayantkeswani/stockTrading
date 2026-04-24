"""Tests for indicators/global_market.py — pure functions, no DB/Redis."""

from app.core.enums import DayBias
from app.indicators.global_market import (
    GlobalCues,
    combined_global_score,
    compute_pre_open_gap,
    global_alignment_factor,
    overnight_bias,
)


class TestComputePreOpenGap:
    def test_gap_up(self):
        assert compute_pre_open_gap(24500, 24000) == pytest.approx(24500 / 24000 * 100 - 100, abs=0.01)

    def test_gap_down(self):
        gap = compute_pre_open_gap(23800, 24000)
        assert gap < 0

    def test_zero_prev_close(self):
        assert compute_pre_open_gap(24000, 0) == 0.0


class TestOvernightBias:
    def test_positive_signals_bullish(self):
        assert overnight_bias(0.5, 0.4, 14.0) == DayBias.BULLISH

    def test_negative_signals_bearish(self):
        assert overnight_bias(-0.5, -0.8, 14.0) == DayBias.BEARISH

    def test_neutral_range(self):
        assert overnight_bias(0.1, 0.1, 14.0) == DayBias.NEUTRAL

    def test_high_vix_bearish(self):
        assert overnight_bias(0.2, 0.2, 26.0) == DayBias.BEARISH

    def test_none_values_neutral(self):
        assert overnight_bias(None, None, None) == DayBias.NEUTRAL


class TestCombinedGlobalScore:
    def test_strong_bullish(self):
        cues = GlobalCues(
            dow_futures_pct=1.5, sp500_close_pct=1.2, nasdaq_close_pct=1.8,
            crude_pct=-0.5, usdinr_pct=-0.2, dxy_pct=-0.3,
        )
        score = combined_global_score(cues)
        assert score > 0.5

    def test_strong_bearish(self):
        cues = GlobalCues(
            dow_futures_pct=-1.5, sp500_close_pct=-1.2, nasdaq_close_pct=-1.8,
            crude_pct=1.5, usdinr_pct=0.5, dxy_pct=0.4,
        )
        score = combined_global_score(cues)
        assert score < -0.5

    def test_all_none_returns_zero(self):
        assert combined_global_score(GlobalCues()) == 0.0

    def test_score_bounded(self):
        cues = GlobalCues(dow_futures_pct=10.0, sp500_close_pct=10.0)
        score = combined_global_score(cues)
        assert -1.0 <= score <= 1.0


class TestGlobalAlignmentFactor:
    def test_bullish_global_ce_trade(self):
        cues = GlobalCues(global_score=0.8)
        factor = global_alignment_factor(cues, "CE")
        assert factor > 0.8

    def test_bearish_global_pe_trade(self):
        cues = GlobalCues(global_score=-0.8)
        factor = global_alignment_factor(cues, "PE")
        assert factor > 0.8

    def test_misaligned(self):
        cues = GlobalCues(global_score=0.8)
        factor = global_alignment_factor(cues, "PE")
        assert factor < 0.2


import pytest
