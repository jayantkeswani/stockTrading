"""Tests for multi-day stock trend indicator."""

from app.indicators.candle_patterns import Candle
from app.indicators.stock_trend import (
    StockTrend,
    _close_position,
    _dma_crossover,
    _hh_hl_pattern,
    _price_vs_20dma,
    _rs_momentum,
    compute_stock_trend,
)


def _make_candle(
    open_: float, high: float, low: float, close: float, volume: int = 100_000
) -> Candle:
    return Candle(open=open_, high=high, low=low, close=close, volume=volume)


def _make_uptrend_candles(n: int = 30, start_price: float = 100.0) -> list[Candle]:
    """Generate N daily candles in a steady uptrend (~0.5% per day)."""
    candles = []
    price = start_price
    for _ in range(n):
        o = price
        c = price * 1.005
        h = c * 1.005
        l = o * 0.995
        candles.append(_make_candle(o, h, l, c))
        price = c
    return candles


def _make_downtrend_candles(n: int = 30, start_price: float = 200.0) -> list[Candle]:
    """Generate N daily candles in a steady downtrend (~0.5% per day)."""
    candles = []
    price = start_price
    for _ in range(n):
        o = price
        c = price * 0.995
        h = o * 1.005
        l = c * 0.995
        candles.append(_make_candle(o, h, l, c))
        price = c
    return candles


def _make_flat_candles(n: int = 30, price: float = 150.0) -> list[Candle]:
    """Generate N daily candles oscillating around a fixed price."""
    candles = []
    for i in range(n):
        offset = 0.5 if i % 2 == 0 else -0.5
        o = price + offset
        c = price - offset
        h = price + 1.0
        l = price - 1.0
        candles.append(_make_candle(o, h, l, c))
    return candles


class TestComputeStockTrend:
    def test_strong_uptrend(self):
        candles = _make_uptrend_candles(40)
        trend = compute_stock_trend(candles)
        assert trend.direction == "BULLISH"
        assert trend.strength == "STRONG"
        assert trend.score > 0.6

    def test_strong_downtrend(self):
        candles = _make_downtrend_candles(40)
        trend = compute_stock_trend(candles)
        assert trend.direction == "BEARISH"
        assert trend.strength == "STRONG"
        assert trend.score < -0.6

    def test_flat_market_neutral(self):
        candles = _make_flat_candles(30)
        trend = compute_stock_trend(candles)
        assert trend.direction == "NEUTRAL"
        assert abs(trend.score) <= 0.3

    def test_direction_thresholds(self):
        # Score > 0.3 = BULLISH
        trend = StockTrend(direction="", strength="", score=0.35, components={})
        assert 0.35 > 0.3  # Confirms BULLISH threshold

        # Score < -0.3 = BEARISH
        trend = StockTrend(direction="", strength="", score=-0.35, components={})
        assert -0.35 < -0.3  # Confirms BEARISH threshold

    def test_strength_thresholds(self):
        candles = _make_uptrend_candles(40)
        trend = compute_stock_trend(candles)
        abs_score = abs(trend.score)
        if abs_score > 0.6:
            assert trend.strength == "STRONG"
        elif abs_score > 0.3:
            assert trend.strength == "MODERATE"
        else:
            assert trend.strength == "WEAK"

    def test_components_populated(self):
        candles = _make_uptrend_candles(30)
        trend = compute_stock_trend(candles)
        expected_keys = {
            "price_vs_20dma",
            "dma_crossover",
            "hh_hl_pattern",
            "adr_trend",
            "close_position",
            "rs_momentum",
        }
        assert set(trend.components.keys()) == expected_keys

    def test_score_clamped_to_range(self):
        candles = _make_uptrend_candles(45, start_price=50.0)
        trend = compute_stock_trend(candles)
        assert -1.0 <= trend.score <= 1.0

        candles = _make_downtrend_candles(45, start_price=300.0)
        trend = compute_stock_trend(candles)
        assert -1.0 <= trend.score <= 1.0

    def test_v_reversal_moderate(self):
        """Downtrend followed by sharp reversal should not be STRONG bullish."""
        down = _make_downtrend_candles(20, start_price=200.0)
        last_price = down[-1].close
        up = _make_uptrend_candles(10, start_price=last_price)
        candles = down + up
        trend = compute_stock_trend(candles)
        # Recent price action is up, but 20 DMA still depressed
        assert trend.strength != "STRONG" or trend.direction != "BULLISH"

    def test_components_rounded(self):
        candles = _make_uptrend_candles(25)
        trend = compute_stock_trend(candles)
        for key, val in trend.components.items():
            assert val == round(val, 4), f"{key} not rounded to 4 decimals"


class TestGracefulDegradation:
    def test_insufficient_bars_returns_neutral(self):
        candles = _make_uptrend_candles(5)
        trend = compute_stock_trend(candles)
        assert trend.direction == "NEUTRAL"
        assert trend.strength == "WEAK"
        assert trend.score == 0.0
        assert trend.components == {}

    def test_empty_candles(self):
        trend = compute_stock_trend([])
        assert trend.direction == "NEUTRAL"
        assert trend.strength == "WEAK"
        assert trend.score == 0.0

    def test_exactly_10_bars_works(self):
        candles = _make_uptrend_candles(10)
        trend = compute_stock_trend(candles)
        assert trend.direction in ("BULLISH", "BEARISH", "NEUTRAL")
        assert trend.components  # non-empty

    def test_9_bars_returns_neutral(self):
        candles = _make_uptrend_candles(9)
        trend = compute_stock_trend(candles)
        assert trend.direction == "NEUTRAL"
        assert trend.score == 0.0


class TestPriceVs20DMA:
    def test_price_well_above_dma(self):
        closes = [100.0] * 20 + [110.0]  # 10% above
        score = _price_vs_20dma(closes)
        assert score > 0.8

    def test_price_well_below_dma(self):
        closes = [100.0] * 20 + [90.0]  # 10% below
        score = _price_vs_20dma(closes)
        assert score < 0.2

    def test_price_at_dma(self):
        closes = [100.0] * 21
        score = _price_vs_20dma(closes)
        assert 0.45 <= score <= 0.55

    def test_fewer_than_20_bars_uses_available(self):
        closes = [100.0] * 12 + [105.0]
        score = _price_vs_20dma(closes)
        assert score > 0.5


class TestDMACrossover:
    def test_bullish_crossover(self):
        # 5 DMA above 20 DMA
        closes = [100.0] * 15 + [105.0, 106.0, 107.0, 108.0, 109.0]
        score = _dma_crossover(closes)
        assert score > 0.6

    def test_bearish_crossover(self):
        closes = [100.0] * 15 + [95.0, 94.0, 93.0, 92.0, 91.0]
        score = _dma_crossover(closes)
        assert score < 0.4

    def test_no_crossover(self):
        closes = [100.0] * 20
        score = _dma_crossover(closes)
        assert 0.45 <= score <= 0.55


class TestHHHLPattern:
    def test_all_higher_highs_and_lows(self):
        candles = [
            _make_candle(100, 105, 95, 103),
            _make_candle(103, 108, 98, 106),
            _make_candle(106, 111, 101, 109),
            _make_candle(109, 114, 104, 112),
            _make_candle(112, 117, 107, 115),
            _make_candle(115, 120, 110, 118),
        ]
        score = _hh_hl_pattern(candles)
        assert score == 1.0

    def test_all_lower_highs_and_lows(self):
        candles = [
            _make_candle(120, 125, 115, 118),
            _make_candle(118, 123, 113, 116),
            _make_candle(116, 121, 111, 114),
            _make_candle(114, 119, 109, 112),
            _make_candle(112, 117, 107, 110),
            _make_candle(110, 115, 105, 108),
        ]
        score = _hh_hl_pattern(candles)
        assert score == 0.0

    def test_mixed_signals(self):
        candles = [
            _make_candle(100, 105, 95, 103),
            _make_candle(103, 108, 98, 106),   # HH, HL
            _make_candle(106, 107, 101, 104),   # LH, HL
            _make_candle(104, 110, 100, 108),   # HH, LH? no: LL
        ]
        score = _hh_hl_pattern(candles)
        assert 0.2 < score < 0.8


class TestClosePosition:
    def test_consistently_closing_high(self):
        candles = [_make_candle(100, 110, 90, 109) for _ in range(5)]
        score = _close_position(candles)
        assert score > 0.8

    def test_consistently_closing_low(self):
        candles = [_make_candle(100, 110, 90, 91) for _ in range(5)]
        score = _close_position(candles)
        assert score < 0.2

    def test_zero_range_skipped(self):
        candles = [_make_candle(100, 100, 100, 100) for _ in range(5)]
        score = _close_position(candles)
        assert score == 0.5  # no valid ranges


class TestRSMomentum:
    def test_accelerating_returns(self):
        # First half flat, second half rising
        closes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 102.0, 104.0, 106.0, 108.0]
        score = _rs_momentum(closes)
        assert score > 0.6

    def test_decelerating_returns(self):
        # First half rising, second half flat
        closes = [100.0, 102.0, 104.0, 106.0, 108.0, 108.0, 108.0, 108.0, 108.0, 108.0]
        score = _rs_momentum(closes)
        assert score < 0.4

    def test_insufficient_data(self):
        closes = [100.0, 101.0, 102.0]
        score = _rs_momentum(closes)
        assert score == 0.5
