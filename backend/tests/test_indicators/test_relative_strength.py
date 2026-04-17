"""Tests for relative strength indicator."""

from app.indicators.relative_strength import (
    compute_50_dma,
    compute_rs_raw_score,
    is_above_50_dma,
    percentile_rank_rs,
)


class TestRS:
    def test_insufficient_data(self):
        assert compute_rs_raw_score([100] * 30) == 0.0

    def test_uptrend_positive(self):
        closes = [100 + i for i in range(252)]
        score = compute_rs_raw_score(closes)
        assert score > 0  # Raw return is positive for uptrend

    def test_downtrend_negative(self):
        closes = [300 - i for i in range(252)]
        score = compute_rs_raw_score(closes)
        assert score < 0  # Raw return is negative for downtrend

    def test_flat_near_zero(self):
        closes = [100.0] * 252
        score = compute_rs_raw_score(closes)
        assert score == 0.0


class TestPercentileRank:
    def test_empty(self):
        assert percentile_rank_rs({}) == {}

    def test_single_stock(self):
        result = percentile_rank_rs({"A": 10.0})
        assert result["A"] == 50.0

    def test_ranking_order(self):
        raw = {"WORST": -20.0, "MID": 5.0, "BEST": 30.0}
        ranked = percentile_rank_rs(raw)
        assert ranked["WORST"] < ranked["MID"] < ranked["BEST"]
        assert ranked["WORST"] >= 1.0
        assert ranked["BEST"] <= 99.0

    def test_two_stocks(self):
        raw = {"LOW": 1.0, "HIGH": 10.0}
        ranked = percentile_rank_rs(raw)
        assert ranked["LOW"] == 1.0
        assert ranked["HIGH"] == 99.0


class Test50DMA:
    def test_insufficient_data(self):
        assert compute_50_dma([100.0] * 30) is None

    def test_exact_50_points(self):
        closes = list(range(1, 51))
        dma = compute_50_dma(closes)
        assert dma == sum(range(1, 51)) / 50

    def test_uses_last_50(self):
        closes = [0.0] * 50 + [100.0] * 50
        dma = compute_50_dma(closes)
        assert dma == 100.0


class TestAbove50DMA:
    def test_above(self):
        closes = [100.0] * 50
        assert is_above_50_dma(110.0, closes) is True

    def test_below(self):
        closes = [100.0] * 50
        assert is_above_50_dma(90.0, closes) is False

    def test_insufficient_data(self):
        assert is_above_50_dma(100.0, [100.0] * 30) is None
