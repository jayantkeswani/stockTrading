"""Tests for CAN SLIM scoring functions."""

import pytest

from app.strategies.canslim.scoring import (
    _interpolate,
    compute_canslim_total,
    compute_rs_raw_score,
    score_a,
    score_c,
    score_i,
    score_l,
    score_m,
    score_n,
    score_s,
)


class TestInterpolate:
    def test_at_zero(self):
        assert _interpolate(10.0, zero_at=10.0, full_at=20.0) == 0.0

    def test_at_full(self):
        assert _interpolate(20.0, zero_at=10.0, full_at=20.0) == 100.0

    def test_midpoint(self):
        assert _interpolate(15.0, zero_at=10.0, full_at=20.0) == 50.0

    def test_below_zero(self):
        assert _interpolate(5.0, zero_at=10.0, full_at=20.0) == 0.0

    def test_above_full(self):
        assert _interpolate(25.0, zero_at=10.0, full_at=20.0) == 100.0

    def test_inverted_lower_is_better(self):
        # D/E: 2.0 = zero, 0.5 = full
        assert _interpolate(2.0, zero_at=2.0, full_at=0.5) == 0.0
        assert _interpolate(0.5, zero_at=2.0, full_at=0.5) == 100.0
        assert _interpolate(1.25, zero_at=2.0, full_at=0.5) == 50.0


class TestScoreC:
    def test_no_data_returns_zero(self):
        assert score_c(None, None, None) == 0.0

    def test_strong_growth(self):
        score = score_c(30.0, 30.0, True)
        assert score > 80

    def test_negative_growth(self):
        score = score_c(-10.0, -5.0, False)
        assert score < 20

    def test_moderate_growth(self):
        score = score_c(15.0, 15.0, None)
        assert 30 < score < 70

    def test_acceleration_bonus(self):
        accel = score_c(20.0, 20.0, True)
        no_accel = score_c(20.0, 20.0, False)
        assert accel > no_accel


class TestScoreA:
    def test_no_data(self):
        # With all None: growth=0, roe=0, opm=50 (neutral default) → 50*0.20 = 10
        assert score_a(None, None, None) == 10.0

    def test_strong_annual(self):
        score = score_a(30.0, 25.0, 18.0)
        assert score > 80

    def test_weak_annual(self):
        score = score_a(3.0, 3.0, 3.0)
        assert score < 10


class TestScoreN:
    def test_no_data(self):
        assert score_n(None) == 0.0

    def test_at_52w_high(self):
        score = score_n(0.0)  # 0% from 52w high = at the high
        assert score == 100.0

    def test_far_from_high(self):
        score = score_n(30.0)  # 30% below
        assert score == 0.0

    def test_within_range(self):
        score = score_n(10.0)  # 10% below
        assert 50 < score < 90


class TestScoreS:
    def test_ideal_supply(self):
        score = score_s(free_float_pct=30.0, volume_ratio=2.0, debt_to_equity=0.3)
        assert score > 80

    def test_poor_supply(self):
        score = score_s(free_float_pct=90.0, volume_ratio=0.3, debt_to_equity=3.0)
        assert score < 20


class TestScoreL:
    def test_no_data(self):
        assert score_l(None) == 0.0

    def test_strong_leader(self):
        assert score_l(95.0) == 100.0

    def test_laggard(self):
        assert score_l(40.0) == 0.0

    def test_threshold(self):
        score = score_l(80.0)
        assert 70 < score < 85


class TestScoreI:
    def test_no_data(self):
        score = score_i(None, None)
        assert score == 50.0  # Both default to neutral

    def test_rising_institutional(self):
        score = score_i(2.0, 1.5)
        assert score > 80

    def test_declining_institutional(self):
        score = score_i(-2.0, -1.5)
        assert score < 20


class TestScoreM:
    def test_bullish_market(self):
        score = score_m(nifty_above_50dma=True, india_vix=12.0)
        assert score > 80

    def test_bearish_market(self):
        score = score_m(nifty_above_50dma=False, india_vix=28.0)
        assert score < 20

    def test_mixed_signals(self):
        # NIFTY above DMA (100*0.6=60) + VIX 22 (30% of range → 30*0.4=12) = 72
        score = score_m(nifty_above_50dma=True, india_vix=22.0)
        assert 60 < score < 80


class TestComposite:
    def test_perfect_scores(self):
        total = compute_canslim_total(100, 100, 100, 100, 100, 100, 100)
        assert total == 100.0

    def test_zero_scores(self):
        total = compute_canslim_total(0, 0, 0, 0, 0, 0, 0)
        assert total == 0.0

    def test_mixed_scores(self):
        total = compute_canslim_total(80, 70, 60, 50, 90, 40, 85)
        assert 60 < total < 80

    def test_custom_weights(self):
        weights = {"C": 1.0, "A": 0, "N": 0, "S": 0, "L": 0, "I": 0, "M": 0}
        total = compute_canslim_total(80, 0, 0, 0, 0, 0, 0, weights=weights)
        assert total == 80.0


class TestRSRawScore:
    def test_insufficient_data(self):
        assert compute_rs_raw_score([100.0] * 30) == 0.0

    def test_steady_uptrend(self):
        # Price going up steadily over a year
        closes = [100 + i * 0.5 for i in range(252)]
        score = compute_rs_raw_score(closes)
        assert score > 0  # Positive raw return

    def test_steady_downtrend(self):
        # Price going down steadily
        closes = [200 - i * 0.5 for i in range(252)]
        score = compute_rs_raw_score(closes)
        assert score < 0  # Negative raw return

    def test_flat_price(self):
        closes = [100.0] * 252
        score = compute_rs_raw_score(closes)
        assert score == 0.0  # No return
