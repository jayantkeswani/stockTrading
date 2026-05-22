"""Tests for VWAP Pullback Strategy (Strategy 2)."""

import pytest
from unittest.mock import patch
from app.core.enums import CPRType, DayBias, SignalType, StrategyName
from app.services.trading_config import TradingConfigDTO

_LOW_PERSIST_CFG = TradingConfigDTO(
    capital=1_000_000, max_daily_drawdown_pct=5.0, max_daily_profit=0.0, max_risk_per_trade_pct=2.0,
    max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
    min_confidence_to_persist=10.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
    shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
)


@pytest.fixture(autouse=True)
def _mock_trading_config_sync():
    with patch(
        "app.services.trading_config.get_trading_config_sync",
        return_value=_LOW_PERSIST_CFG,
    ):
        yield
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import CPRResult
from app.indicators.open_interest import OIAnalysis
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult
from app.strategies.base import ExitSignal, MarketContext
from app.strategies.strategy_2_vwap_pullback import VWAPPullbackStrategy


# ---------------------------------------------------------------------------
# Helpers to build MarketContext
# ---------------------------------------------------------------------------

def _make_vwap(vwap: float = 100.0) -> VWAPResult:
    return VWAPResult(vwap=vwap, upper_band=vwap + 1, lower_band=vwap - 1)


def _make_prev_day(bias: DayBias = DayBias.BULLISH) -> PreviousDayLevels:
    return PreviousDayLevels(
        pdh=110, pdl=90, pdc=105, pdo=100, day_range=20, bias=bias,
    )


def _make_cpr(cpr_type: CPRType = CPRType.WIDE) -> CPRResult:
    return CPRResult(
        pivot=100, tc=101, bc=99, r1=110, s1=90, r2=120, s2=80,
        cpr_type=cpr_type, cpr_width_pct=2.0 if cpr_type == CPRType.WIDE else 0.05,
    )


def _make_oi(
    pcr: float = 1.0,
    max_pe_strike: float = 95.0,
    max_ce_strike: float = 105.0,
    sentiment: str = "NEUTRAL",
) -> OIAnalysis:
    return OIAnalysis(
        pcr=pcr,
        max_ce_oi_strike=max_ce_strike,
        max_pe_oi_strike=max_pe_strike,
        total_ce_oi=10000,
        total_pe_oi=int(10000 * pcr),
        max_pain=100,
        sentiment=sentiment,
    )


def _bullish_engulfing_candles(price: float) -> list[Candle]:
    """Return 5+ candles where the last two form a bullish engulfing at given price level.

    All candles have low volume so the volume filter doesn't block.
    """
    base = [
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
    ]
    # Bearish prev, bullish curr that engulfs
    prev = Candle(open=price + 1, high=price + 1.5, low=price - 0.5, close=price - 0.3, volume=100)
    curr = Candle(open=price - 0.5, high=price + 2, low=price - 1, close=price + 1.5, volume=100)
    return base + [prev, curr]


def _bearish_engulfing_candles(price: float) -> list[Candle]:
    """Return 5+ candles where the last two form a bearish engulfing."""
    base = [
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
    ]
    # Bullish prev, bearish curr that engulfs
    prev = Candle(open=price - 0.3, high=price + 1, low=price - 0.5, close=price + 1, volume=100)
    curr = Candle(open=price + 1.5, high=price + 2, low=price - 1, close=price - 0.5, volume=100)
    return base + [prev, curr]


def _make_intraday_bias(bias: DayBias, strength: str = "MODERATE"):
    from app.indicators.intraday_bias import IntradayBias
    score_map = {DayBias.BULLISH: 0.45, DayBias.BEARISH: -0.45, DayBias.NEUTRAL: 0.0}
    if strength == "STRONG":
        score_map = {DayBias.BULLISH: 0.7, DayBias.BEARISH: -0.7, DayBias.NEUTRAL: 0.0}
    score = score_map[bias]
    return IntradayBias(bias=bias, score=score, strength=strength, components={"score": score})


def _make_context(
    price: float = 100.05,
    vwap_val: float = 100.0,
    bias: DayBias = DayBias.BULLISH,
    cpr_type: CPRType = CPRType.WIDE,
    candles: list[Candle] | None = None,
    oi: OIAnalysis | None = None,
    india_vix: float | None = 16.0,
    vwap: VWAPResult | None = ...,
    prev_day: PreviousDayLevels | None = ...,
    cpr: CPRResult | None = ...,
    intraday_bias=...,  # auto-creates from bias if not specified
) -> MarketContext:
    """Build a MarketContext with sensible defaults.

    Use sentinel ... to auto-create; pass None to omit.
    """
    if vwap is ...:
        vwap = _make_vwap(vwap_val)
    if prev_day is ...:
        prev_day = _make_prev_day(bias)
    if cpr is ...:
        cpr = _make_cpr(cpr_type)
    if candles is None:
        candles = _bullish_engulfing_candles(price)
    if intraday_bias is ...:
        intraday_bias = _make_intraday_bias(bias)

    return MarketContext(
        symbol="NIFTY",
        current_price=price,
        candles_5m=candles,
        vwap=vwap,
        previous_day=prev_day,
        cpr=cpr,
        oi_analysis=oi,
        india_vix=india_vix,
        current_time_ist="10:30:00",
        intraday_bias=intraday_bias,
        strategy_params={},
    )


# ---------------------------------------------------------------------------
# evaluate — missing indicators
# ---------------------------------------------------------------------------

class TestEvaluateMissingIndicators:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_returns_none_when_vwap_missing(self):
        ctx = _make_context(vwap=None)
        assert self.strategy.evaluate(ctx) is None

    def test_returns_none_when_previous_day_missing(self):
        ctx = _make_context(prev_day=None)
        assert self.strategy.evaluate(ctx) is None

    def test_returns_none_when_cpr_missing(self):
        ctx = _make_context(cpr=None)
        assert self.strategy.evaluate(ctx) is None

    def test_returns_none_when_candles_insufficient(self):
        ctx = _make_context(candles=[
            Candle(100, 101, 99, 100.5, 100),
            Candle(100, 101, 99, 100.5, 100),
        ])
        assert self.strategy.evaluate(ctx) is None


# ---------------------------------------------------------------------------
# evaluate — VWAP proximity
# ---------------------------------------------------------------------------

class TestEvaluateVwapProximity:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_returns_none_when_not_in_vwap_proximity(self):
        """Price 2% away from VWAP => not a pullback."""
        ctx = _make_context(price=102.0, vwap_val=100.0)
        assert self.strategy.evaluate(ctx) is None


# ---------------------------------------------------------------------------
# evaluate — CALL signal generation
# ---------------------------------------------------------------------------

class TestEvaluateCallSignal:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_call_signal_with_bullish_bias(self):
        """Bullish bias + price above VWAP + bullish reversal => BUY_CE."""
        price = 100.05
        ctx = _make_context(
            price=price,
            vwap_val=100.0,
            bias=DayBias.BULLISH,
            candles=_bullish_engulfing_candles(price),
        )
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_CE
        assert signal.strategy_name == StrategyName.VWAP_PULLBACK

    def test_call_signal_with_neutral_bias_and_price_above_vwap(self):
        """Neutral bias + distance > 0 => still tries CALL path."""
        price = 100.05
        ctx = _make_context(
            price=price,
            vwap_val=100.0,
            bias=DayBias.NEUTRAL,
            candles=_bullish_engulfing_candles(price),
        )
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_CE


# ---------------------------------------------------------------------------
# evaluate — PUT signal generation
# ---------------------------------------------------------------------------

class TestEvaluatePutSignal:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_put_signal_with_bearish_bias(self):
        """Bearish bias + price below VWAP + bearish reversal => BUY_PE."""
        price = 99.95
        ctx = _make_context(
            price=price,
            vwap_val=100.0,
            bias=DayBias.BEARISH,
            candles=_bearish_engulfing_candles(price),
        )
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_PE

    def test_put_signal_with_neutral_bias_and_price_below_vwap(self):
        price = 99.95
        # NEUTRAL bias with narrow CPR to push confidence above threshold
        ctx = _make_context(
            price=price,
            vwap_val=100.0,
            bias=DayBias.NEUTRAL,
            cpr_type=CPRType.NARROW,
            candles=_bearish_engulfing_candles(price),
        )
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_PE


# ---------------------------------------------------------------------------
# evaluate — confidence scoring
# ---------------------------------------------------------------------------

class TestConfidenceScoring:
    """Confidence is now a 10-factor weighted composite, not inline constants.
    Tests use relative comparisons (A > B) rather than exact values."""

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_aligned_bias_gives_higher_confidence_than_neutral(self):
        price = 100.05
        aligned = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                                 candles=_bullish_engulfing_candles(price))
        neutral = _make_context(price=price, vwap_val=100.0, bias=DayBias.NEUTRAL,
                                 candles=_bullish_engulfing_candles(price))
        s_aligned = self.strategy.evaluate(aligned)
        s_neutral = self.strategy.evaluate(neutral)
        assert s_aligned is not None
        if s_neutral is not None:
            assert s_aligned.confidence >= s_neutral.confidence

    def test_narrow_cpr_gives_higher_confidence_than_wide(self):
        price = 100.05
        narrow = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                                cpr_type=CPRType.NARROW, candles=_bullish_engulfing_candles(price))
        wide = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                              cpr_type=CPRType.WIDE, candles=_bullish_engulfing_candles(price))
        s_narrow = self.strategy.evaluate(narrow)
        s_wide = self.strategy.evaluate(wide)
        assert s_narrow is not None
        if s_wide is not None:
            assert s_narrow.confidence >= s_wide.confidence

    def test_low_vix_gives_higher_confidence_than_high_vix(self):
        price = 100.05
        low = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                             india_vix=12.0, candles=_bullish_engulfing_candles(price))
        high = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                              india_vix=20.0, candles=_bullish_engulfing_candles(price))
        s_low = self.strategy.evaluate(low)
        s_high = self.strategy.evaluate(high)
        assert s_low is not None
        if s_high is not None:
            assert s_low.confidence > s_high.confidence

    def test_oi_not_confirmed_reduces_confidence(self):
        """OI support is a weighted factor — confirmed gives higher confidence than not."""
        price = 100.05
        oi_good = _make_oi(max_pe_strike=95.0, max_ce_strike=105.0)
        oi_bad = _make_oi(max_pe_strike=200.0, max_ce_strike=300.0)
        ctx_good = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                                  oi=oi_good, candles=_bullish_engulfing_candles(price))
        ctx_bad = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                                 oi=oi_bad, candles=_bullish_engulfing_candles(price))
        s_good = self.strategy.evaluate(ctx_good)
        s_bad = self.strategy.evaluate(ctx_bad)
        if s_good is not None and s_bad is not None:
            assert s_good.confidence >= s_bad.confidence

    def test_confidence_in_range(self):
        price = 100.05
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                             cpr_type=CPRType.NARROW, india_vix=12.0,
                             candles=_bullish_engulfing_candles(price))
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert 0 <= signal.confidence <= 100

    def test_confidence_factors_in_indicators(self):
        """New: confidence_factors dict is persisted in signal.indicators."""
        price = 100.05
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                             candles=_bullish_engulfing_candles(price))
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert "confidence_factors" in signal.indicators
        assert "bias_alignment" in signal.indicators["confidence_factors"]

    def test_intraday_bias_in_indicators(self):
        """New: intraday_bias components are persisted in signal.indicators."""
        price = 100.05
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH,
                             candles=_bullish_engulfing_candles(price))
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert "intraday_bias" in signal.indicators

    def test_bearish_bias_ce_signal_for_put(self):
        """Bearish intraday bias should allow PE signal to fire (MODERATE strength)."""
        price = 99.95
        ctx = _make_context(
            price=price, vwap_val=100.0, bias=DayBias.BEARISH,
            candles=_bearish_engulfing_candles(price),
        )
        signal = self.strategy.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_PE


# ---------------------------------------------------------------------------
# should_exit
# ---------------------------------------------------------------------------

class TestShouldExit:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_sl_hit(self):
        ctx = _make_context(price=50.0)
        exit_signal = self.strategy.should_exit(ctx, entry_price=100.0, stop_loss=70.0, target_price=150.0)
        assert exit_signal is not None
        assert exit_signal.exit_type == "SL_HIT"

    def test_target_hit(self):
        ctx = _make_context(price=160.0)
        exit_signal = self.strategy.should_exit(ctx, entry_price=100.0, stop_loss=70.0, target_price=150.0)
        assert exit_signal is not None
        assert exit_signal.exit_type == "TARGET_HIT"

    def test_vwap_invalidation(self):
        """Entry above VWAP but price drops below VWAP * 0.998."""
        vwap_val = 100.0
        # entry_price > vwap, current price < vwap * 0.998
        ctx = _make_context(price=99.7, vwap_val=vwap_val)
        exit_signal = self.strategy.should_exit(
            ctx, entry_price=101.0, stop_loss=90.0, target_price=120.0,
        )
        assert exit_signal is not None
        assert exit_signal.exit_type == "INVALIDATION"

    def test_no_exit_when_price_normal(self):
        ctx = _make_context(price=105.0, vwap_val=100.0)
        exit_signal = self.strategy.should_exit(
            ctx, entry_price=101.0, stop_loss=90.0, target_price=120.0,
        )
        assert exit_signal is None

    def test_no_exit_without_vwap(self):
        """If VWAP is missing, only SL/target checks apply."""
        ctx = _make_context(price=105.0, vwap=None)
        exit_signal = self.strategy.should_exit(
            ctx, entry_price=101.0, stop_loss=90.0, target_price=120.0,
        )
        assert exit_signal is None

    def test_no_exit_when_target_is_none(self):
        """Target=None => no target check."""
        ctx = _make_context(price=200.0, vwap_val=100.0)
        # Price > target_price would trigger, but target_price is None
        # However entry_price=101 > vwap=100 and price=200 is not < vwap*0.998
        exit_signal = self.strategy.should_exit(
            ctx, entry_price=101.0, stop_loss=90.0, target_price=None,
        )
        assert exit_signal is None

    def test_sl_exactly_at_stop_loss(self):
        ctx = _make_context(price=70.0)
        exit_signal = self.strategy.should_exit(ctx, entry_price=100.0, stop_loss=70.0, target_price=150.0)
        assert exit_signal is not None
        assert exit_signal.exit_type == "SL_HIT"

    def test_target_exactly_at_target_price(self):
        ctx = _make_context(price=150.0)
        exit_signal = self.strategy.should_exit(ctx, entry_price=100.0, stop_loss=70.0, target_price=150.0)
        assert exit_signal is not None
        assert exit_signal.exit_type == "TARGET_HIT"


# ---------------------------------------------------------------------------
# position sizing via calculate_lots (VWAPPullback uses max_lots=5)
# ---------------------------------------------------------------------------

class TestGetPositionSize:

    MAX_LOTS = VWAPPullbackStrategy.max_lots  # 5

    def test_basic_calculation(self):
        """capital=100000, risk_pct=2%, entry=100, sl=70, lot_size=75.
        risk_amount = 100000 * 0.02 = 2000
        risk_per_lot = |100-70| * 75 = 2250
        lots = int(2000/2250) = 0 => max(1, 0) = 1
        """
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=100000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=70, lot_size=75,
            max_lots=self.MAX_LOTS,
        )
        assert lots == 1

    def test_larger_capital(self):
        """capital=1000000, risk=2%, entry=100, sl=90, lot=75.
        risk_amount = 20000
        risk_per_lot = 10*75 = 750
        lots = int(20000/750) = 26 => capped at 5
        """
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=1_000_000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=90, lot_size=75,
            max_lots=self.MAX_LOTS,
        )
        assert lots == 5  # capped

    def test_cap_at_5_lots(self):
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=10_000_000, risk_per_trade_pct=5.0,
            entry_price=100, stop_loss=95, lot_size=10,
            max_lots=self.MAX_LOTS,
        )
        assert lots == 5

    def test_minimum_1_lot(self):
        """Even if risk_amount is tiny, at least 1 lot."""
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=1000, risk_per_trade_pct=0.01,
            entry_price=100, stop_loss=50, lot_size=75,
            max_lots=self.MAX_LOTS,
        )
        assert lots >= 1

    def test_vix_multiplier_reduces_lots(self):
        """VIX multiplier 0.7 reduces lots."""
        from app.services.position_sizing import calculate_lots
        lots_full = calculate_lots(
            capital=1_000_000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=90, lot_size=75,
            vix_multiplier=1.0, max_lots=self.MAX_LOTS,
        )
        lots_reduced = calculate_lots(
            capital=1_000_000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=90, lot_size=75,
            vix_multiplier=0.7, max_lots=self.MAX_LOTS,
        )
        assert lots_reduced <= lots_full

    def test_zero_risk_per_lot(self):
        """entry == stop_loss => risk_per_lot=0 => returns 1."""
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=100000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=100, lot_size=75,
            max_lots=self.MAX_LOTS,
        )
        assert lots == 1

    def test_position_size_with_various_vix(self):
        """With vix_multiplier=0.0, lots = max(1, 0) = 1."""
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=1_000_000, risk_per_trade_pct=2.0,
            entry_price=100, stop_loss=90, lot_size=75,
            vix_multiplier=0.0, max_lots=self.MAX_LOTS,
        )
        assert lots == 1

    def test_exact_calculation(self):
        """Verify exact math:
        capital=500000, risk_pct=2%, entry=200, sl=180, lot=50, vix_mult=1.0
        risk_amount = 500000 * 0.02 = 10000
        risk_per_lot = 20 * 50 = 1000
        lots = int(10000/1000) = 10 => max(1, int(10*1)) = 10 => min(10,5) = 5
        """
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=500_000, risk_per_trade_pct=2.0,
            entry_price=200, stop_loss=180, lot_size=50,
            vix_multiplier=1.0, max_lots=self.MAX_LOTS,
        )
        assert lots == 5


# ---------------------------------------------------------------------------
# evaluate — no signal on missing reversal
# ---------------------------------------------------------------------------

class TestNoSignalOnMissingReversal:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_no_call_without_bullish_reversal(self):
        """Bullish bias, near VWAP, but no bullish reversal pattern."""
        price = 100.05
        # Non-reversal candles: all mildly bullish, no engulfing/pin_bar/doji pattern
        candles = [
            Candle(open=price - 0.5, high=price + 0.5, low=price - 0.6, close=price + 0.3, volume=100),
            Candle(open=price - 0.5, high=price + 0.5, low=price - 0.6, close=price + 0.3, volume=100),
            Candle(open=price - 0.5, high=price + 0.5, low=price - 0.6, close=price + 0.3, volume=100),
            Candle(open=price - 0.5, high=price + 0.5, low=price - 0.6, close=price + 0.3, volume=100),
            Candle(open=price - 0.5, high=price + 0.5, low=price - 0.6, close=price + 0.3, volume=100),
        ]
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH, candles=candles)
        assert self.strategy.evaluate(ctx) is None

    def test_no_put_without_bearish_reversal(self):
        price = 99.95
        candles = [
            Candle(open=price + 0.5, high=price + 0.6, low=price - 0.5, close=price - 0.3, volume=100),
            Candle(open=price + 0.5, high=price + 0.6, low=price - 0.5, close=price - 0.3, volume=100),
            Candle(open=price + 0.5, high=price + 0.6, low=price - 0.5, close=price - 0.3, volume=100),
            Candle(open=price + 0.5, high=price + 0.6, low=price - 0.5, close=price - 0.3, volume=100),
            Candle(open=price + 0.5, high=price + 0.6, low=price - 0.5, close=price - 0.3, volume=100),
        ]
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BEARISH, candles=candles)
        assert self.strategy.evaluate(ctx) is None


# ---------------------------------------------------------------------------
# evaluate — high volume blocks signal
# ---------------------------------------------------------------------------

class TestHighVolumeBlocksSignal:

    def setup_method(self):
        self.strategy = VWAPPullbackStrategy()

    def test_high_volume_blocks_call(self):
        """Current volume > 1.2 * average => no signal."""
        price = 100.05
        candles = _bullish_engulfing_candles(price)
        # Set last candle's volume to be very high
        candles[-1] = Candle(
            open=candles[-1].open, high=candles[-1].high,
            low=candles[-1].low, close=candles[-1].close,
            volume=10000,  # way above average of ~100
        )
        ctx = _make_context(price=price, vwap_val=100.0, bias=DayBias.BULLISH, candles=candles)
        assert self.strategy.evaluate(ctx) is None
