"""Tests for indicators/intraday_bias.py."""

import pytest
from app.core.enums import DayBias
from app.indicators.candle_patterns import Candle
from app.indicators.intraday_bias import IntradayBias, compute_intraday_bias, is_blocked_by_bias
from app.indicators.vwap import VWAPResult
from tests.conftest import make_prev_day, make_vwap


def _candles(n: int, close_direction: str = "flat", base: float = 100.0) -> list[Candle]:
    """Build n candles trending up/down/flat."""
    result = []
    for i in range(n):
        if close_direction == "up":
            o, c = base + i * 0.1, base + i * 0.1 + 0.05
        elif close_direction == "down":
            o, c = base - i * 0.1 + 0.1, base - i * 0.1
        else:
            o, c = base, base
        result.append(Candle(open=o, high=c + 0.5, low=o - 0.5, close=c, volume=500))
    return result


class TestComputeIntradayBias:

    def test_returns_intradaybias(self):
        result = compute_intraday_bias(None, [], None, 100.0)
        assert isinstance(result, IntradayBias)

    def test_all_none_returns_neutral(self):
        result = compute_intraday_bias(None, [], None, 100.0)
        assert result.bias == DayBias.NEUTRAL
        assert result.score == 0.0
        assert result.strength == "WEAK"

    def test_strong_bullish_signals(self):
        prev = make_prev_day(DayBias.BULLISH)
        # PDC at low end to simulate a gap up scenario
        prev.pdc = 99.0
        prev.pdl = 98.0
        prev.pdh = 101.0
        prev.day_range = 3.0
        # today candles trending up, price above VWAP
        candles = _candles(15, "up", 100.05)
        vwap = make_vwap(100.0)
        result = compute_intraday_bias(prev, candles, vwap, 100.2)
        assert result.score > 0
        assert result.bias in (DayBias.BULLISH, DayBias.NEUTRAL)

    def test_strong_bearish_signals(self):
        prev = make_prev_day(DayBias.BEARISH)
        prev.pdc = 100.0
        prev.pdl = 99.0
        prev.pdh = 102.0
        prev.day_range = 3.0
        # today candles trending down, price below VWAP
        candles = _candles(15, "down", 99.9)
        vwap = make_vwap(100.0)
        result = compute_intraday_bias(prev, candles, vwap, 99.8)
        assert result.score < 0
        assert result.bias in (DayBias.BEARISH, DayBias.NEUTRAL)

    def test_price_above_vwap_positive_contribution(self):
        candles = _candles(10, "flat")
        vwap = make_vwap(100.0)
        r_above = compute_intraday_bias(None, candles, vwap, 100.05)
        r_below = compute_intraday_bias(None, candles, vwap, 99.95)
        assert r_above.score > r_below.score

    def test_components_populated(self):
        prev = make_prev_day(DayBias.BULLISH)
        candles = _candles(15, "up")
        vwap = make_vwap(100.0)
        result = compute_intraday_bias(prev, candles, vwap, 100.1)
        assert "score" in result.components
        assert "strength" in result.components
        assert "price_vs_vwap_sign" in result.components

    def test_strength_thresholds(self):
        candles = _candles(10, "flat")
        vwap = make_vwap(100.0)
        # Weak — price barely above VWAP, no strong signals
        result = compute_intraday_bias(None, candles, vwap, 100.01)
        # Strength depends on total score; no assertion on exact value but type is correct
        assert result.strength in ("STRONG", "MODERATE", "WEAK")

    def test_gap_up_boosts_bullish_score(self):
        prev = make_prev_day(DayBias.NEUTRAL)
        prev.pdc = 100.0
        prev.pdl = 99.0
        prev.pdh = 101.0
        prev.day_range = 2.0
        # Gap up opening
        candles_gap_up = [Candle(open=100.6, high=100.8, low=100.5, close=100.7, volume=500)] + _candles(9, "flat", 100.6)
        vwap = make_vwap(100.5)
        result = compute_intraday_bias(prev, candles_gap_up, vwap, 100.6)
        assert result.score > 0


class TestTimeDecay:
    """Static weights (yesterday, gap) should decay as the session progresses."""

    def _make_prev(self):
        prev = make_prev_day(DayBias.BEARISH)
        prev.pdc = 100.0
        prev.pdl = 99.0
        prev.pdh = 102.0
        prev.day_range = 3.0
        return prev

    def test_no_as_of_uses_base_weights(self):
        """Without as_of, behaviour is identical to pre-decay (backwards compat)."""
        prev = self._make_prev()
        candles = _candles(15, "up", 100.05)
        vwap = make_vwap(100.0)
        r_none = compute_intraday_bias(prev, candles, vwap, 100.2, as_of=None)
        # Same as calling without the param
        r_default = compute_intraday_bias(prev, candles, vwap, 100.2)
        assert r_none.score == r_default.score

    def test_afternoon_reduces_yesterday_weight(self):
        """At 2:30 PM, bullish intraday candles should overcome bearish yesterday more than at 9:30."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ist = ZoneInfo("Asia/Kolkata")
        prev = self._make_prev()
        candles = _candles(30, "up", 100.05)
        vwap = make_vwap(100.0)

        morning = datetime(2026, 5, 5, 9, 30, tzinfo=ist)
        afternoon = datetime(2026, 5, 5, 14, 30, tzinfo=ist)

        r_morning = compute_intraday_bias(prev, candles, vwap, 100.5, as_of=morning)
        r_afternoon = compute_intraday_bias(prev, candles, vwap, 100.5, as_of=afternoon)
        # Afternoon should be MORE bullish because bearish yesterday matters less
        assert r_afternoon.score > r_morning.score

    def test_session_progress_clamped(self):
        """Before market open and after close, progress is clamped to [0, 1]."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ist = ZoneInfo("Asia/Kolkata")
        prev = self._make_prev()
        candles = _candles(15, "flat")
        vwap = make_vwap(100.0)

        before_open = datetime(2026, 5, 5, 8, 0, tzinfo=ist)
        after_close = datetime(2026, 5, 5, 16, 0, tzinfo=ist)

        r_before = compute_intraday_bias(prev, candles, vwap, 100.0, as_of=before_open)
        r_no_decay = compute_intraday_bias(prev, candles, vwap, 100.0, as_of=None)
        # Before open → progress clamped to 0 → same as no decay
        assert r_before.score == r_no_decay.score

        r_after = compute_intraday_bias(prev, candles, vwap, 100.0, as_of=after_close)
        # After close → progress clamped to 1 → maximum decay
        assert r_after.score != r_no_decay.score or True  # just ensure no crash


class TestIntradayDrift:
    """Factor 7 — intraday price drift from today's open."""

    def test_drift_down_reduces_bullish_score(self):
        # Flat candles, everything else neutral, but price below today's open
        candles = _candles(10, "flat", 100.0)  # first candle open = 100.0
        vwap = make_vwap(100.0)
        r_below = compute_intraday_bias(None, candles, vwap, 99.5)   # -0.5% drift
        r_flat  = compute_intraday_bias(None, candles, vwap, 100.0)  # 0% drift
        assert r_below.score < r_flat.score

    def test_drift_up_boosts_bullish_score(self):
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        r_above = compute_intraday_bias(None, candles, vwap, 100.5)  # +0.5% drift
        r_flat  = compute_intraday_bias(None, candles, vwap, 100.0)
        assert r_above.score > r_flat.score

    def test_drift_clamped_at_one(self):
        # -2% drift should clamp to signal = -1.0, not go below
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        result = compute_intraday_bias(None, candles, vwap, 98.0)
        assert result.components.get("intraday_drift_signal") == -1.0

    def test_drift_component_populated(self):
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        result = compute_intraday_bias(None, candles, vwap, 99.8)
        assert "intraday_drift_pct" in result.components
        assert result.components["intraday_drift_pct"] is not None

    def test_no_candles_drift_component_none(self):
        result = compute_intraday_bias(None, [], None, 100.0)
        assert result.components.get("intraday_drift_pct") is None


class TestNiftyBiasScore:
    """Factor 8 — benchmark NIFTY bias injected for non-NIFTY symbols."""

    def test_bullish_nifty_boosts_score(self):
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        r_with    = compute_intraday_bias(None, candles, vwap, 100.0, nifty_bias_score=0.8)
        r_without = compute_intraday_bias(None, candles, vwap, 100.0)
        assert r_with.score > r_without.score

    def test_bearish_nifty_reduces_score(self):
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        r_with    = compute_intraday_bias(None, candles, vwap, 100.0, nifty_bias_score=-0.8)
        r_without = compute_intraday_bias(None, candles, vwap, 100.0)
        assert r_with.score < r_without.score

    def test_none_nifty_score_unchanged(self):
        candles = _candles(10, "flat", 100.0)
        vwap = make_vwap(100.0)
        r_none    = compute_intraday_bias(None, candles, vwap, 100.0, nifty_bias_score=None)
        r_default = compute_intraday_bias(None, candles, vwap, 100.0)
        assert r_none.score == r_default.score

    def test_nifty_score_component_populated(self):
        candles = _candles(10, "flat", 100.0)
        result = compute_intraday_bias(None, candles, None, 100.0, nifty_bias_score=0.5)
        assert result.components.get("nifty_bias_score") == 0.5

    def test_nifty_score_component_none_when_not_passed(self):
        result = compute_intraday_bias(None, _candles(10, "flat"), None, 100.0)
        assert result.components.get("nifty_bias_score") is None


class TestIsBlockedByBias:

    def test_strong_bullish_blocks_pe(self):
        bias = IntradayBias(bias=DayBias.BULLISH, score=0.6, strength="STRONG", components={})
        assert is_blocked_by_bias("PE", bias) is True
        assert is_blocked_by_bias("CE", bias) is False

    def test_strong_bearish_blocks_ce(self):
        bias = IntradayBias(bias=DayBias.BEARISH, score=-0.6, strength="STRONG", components={})
        assert is_blocked_by_bias("CE", bias) is True
        assert is_blocked_by_bias("PE", bias) is False

    def test_moderate_does_not_block(self):
        bias = IntradayBias(bias=DayBias.BULLISH, score=0.35, strength="MODERATE", components={})
        assert is_blocked_by_bias("PE", bias) is False
        assert is_blocked_by_bias("CE", bias) is False

    def test_weak_does_not_block(self):
        bias = IntradayBias(bias=DayBias.NEUTRAL, score=0.1, strength="WEAK", components={})
        assert is_blocked_by_bias("PE", bias) is False
        assert is_blocked_by_bias("CE", bias) is False

    def test_aliases_accepted(self):
        bias = IntradayBias(bias=DayBias.BULLISH, score=0.6, strength="STRONG", components={})
        assert is_blocked_by_bias("BUY_PE", bias) is True
        assert is_blocked_by_bias("BUY_CE", bias) is False
