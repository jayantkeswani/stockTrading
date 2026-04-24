"""Tests for indicators/confidence.py."""

import pytest
from app.core.enums import CPRType, DayBias
from app.indicators.confidence import ConfidenceResult, compute_confidence, _WEIGHTS
from app.indicators.intraday_bias import IntradayBias
from tests.conftest import (
    bullish_engulfing_candles, bearish_engulfing_candles,
    make_cpr, make_global_cues, make_oi, make_vwap,
)


def _make_bias(score: float) -> IntradayBias:
    from app.core.enums import DayBias
    bias = DayBias.BULLISH if score > 0.2 else (DayBias.BEARISH if score < -0.2 else DayBias.NEUTRAL)
    strength = "STRONG" if abs(score) >= 0.5 else ("MODERATE" if abs(score) >= 0.2 else "WEAK")
    return IntradayBias(bias=bias, score=score, strength=strength, components={})


class TestComputeConfidence:

    def _call(self, direction="CE", **overrides):
        defaults = dict(
            signal_direction=direction,
            intraday_bias=_make_bias(0.4),
            candles_5m=bullish_engulfing_candles(100.0),
            vwap=make_vwap(100.0),
            oi_analysis=make_oi(pcr=1.2, max_pe_strike=95.0, max_ce_strike=105.0),
            cpr=make_cpr(CPRType.NARROW),
            india_vix=14.5,
            global_cues=make_global_cues(dow_futures_pct=0.4),
            index_sl=99.5,
            index_target=101.0,
            index_entry=100.0,
            current_time_ist="2026-04-24T10:00:00+05:30",
        )
        defaults.update(overrides)
        return compute_confidence(**defaults)

    def test_returns_confidence_result(self):
        result = self._call()
        assert isinstance(result, ConfidenceResult)

    def test_score_in_range(self):
        result = self._call()
        assert 0.0 <= result.score <= 100.0

    def test_all_factors_present(self):
        result = self._call()
        assert set(result.factors.keys()) == set(_WEIGHTS.keys())

    def test_all_factor_values_in_range(self):
        result = self._call()
        for k, v in result.factors.items():
            assert 0.0 <= v <= 1.0, f"Factor {k} = {v} out of [0,1]"

    def test_strong_bullish_bias_boosts_ce(self):
        strong = self._call("CE", intraday_bias=_make_bias(0.8))
        weak = self._call("CE", intraday_bias=_make_bias(0.1))
        assert strong.score > weak.score

    def test_opposing_bias_reduces_ce(self):
        aligned = self._call("CE", intraday_bias=_make_bias(0.6))
        opposing = self._call("CE", intraday_bias=_make_bias(-0.6))
        assert aligned.score > opposing.score

    def test_narrow_cpr_boosts_confidence(self):
        narrow = self._call(cpr=make_cpr(CPRType.NARROW))
        wide = self._call(cpr=make_cpr(CPRType.WIDE))
        assert narrow.score > wide.score

    def test_good_rr_boosts_confidence(self):
        good_rr = self._call(index_sl=99.0, index_target=102.0, index_entry=100.0)  # RR=2
        poor_rr = self._call(index_sl=99.5, index_target=100.5, index_entry=100.0)  # RR=1
        assert good_rr.score > poor_rr.score

    def test_high_vix_penalised(self):
        low_vix = self._call(india_vix=13.0)
        high_vix = self._call(india_vix=20.0)
        assert low_vix.score > high_vix.score

    def test_pe_direction_handled(self):
        result = self._call("PE", intraday_bias=_make_bias(-0.5),
                            candles_5m=bearish_engulfing_candles(100.0))
        assert isinstance(result, ConfidenceResult)
        assert 0.0 <= result.score <= 100.0

    def test_rationale_short_non_empty(self):
        result = self._call()
        assert len(result.rationale_short) > 0

    def test_none_inputs_return_midrange(self):
        result = compute_confidence(
            signal_direction="CE",
            intraday_bias=None,
            candles_5m=[],
            vwap=None,
            oi_analysis=None,
            cpr=None,
            india_vix=None,
            global_cues=None,
            index_sl=None,
            index_target=None,
            index_entry=100.0,
        )
        assert isinstance(result, ConfidenceResult)
        # All factors neutral (0.5) → score ≈ 50
        assert 45 <= result.score <= 55
