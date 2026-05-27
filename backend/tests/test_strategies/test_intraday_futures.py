"""Tests for Strategy 5: Intraday Stock Futures."""

import time
from datetime import date, datetime, time as dt_time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.candle_patterns import Candle
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult
from app.services.trading_config import TradingConfigDTO
from app.strategies.base import StrategySignal
from app.strategies.strategy_5_intraday_futures import (
    IntradayFuturesStrategy,
    get_current_phase,
)

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


def _make_ctx(
    symbol="TCS",
    price=500.0,
    candles_5m=None,
    vwap_val=None,
    candles_daily=None,
    params=None,
    volume_avg_20d=None,
    atr_5m=None,
    previous_day=None,
    today_open=None,
):
    """Build a minimal MarketContext-like object for testing."""
    from app.strategies.base import MarketContext

    if candles_5m is None:
        candles_5m = [Candle(open=495, high=502, low=493, close=500, volume=100000)]
    if candles_daily is None:
        candles_daily = [
            Candle(open=490, high=510, low=480, close=500, volume=500000)
            for _ in range(25)
        ]

    vwap = None
    if vwap_val is not None:
        vwap = VWAPResult(vwap=vwap_val, upper_band=vwap_val + 5, lower_band=vwap_val - 5)

    return MarketContext(
        symbol=symbol,
        current_price=price,
        candles_5m=candles_5m,
        vwap=vwap,
        previous_day=previous_day,
        cpr=None,
        oi_analysis=None,
        india_vix=None,
        current_time_ist="10:00",
        candles_daily=candles_daily,
        volume_avg_20d=volume_avg_20d,
        strategy_params=params or {},
        atr_5m=atr_5m,
        today_open=today_open,
    )


class TestPhaseStateMachine:
    def test_pre_market(self):
        dt = datetime(2026, 4, 27, 8, 0)
        assert get_current_phase(dt) == "PRE_MARKET"

    def test_orb_forming_start(self):
        dt = datetime(2026, 4, 27, 9, 15)
        assert get_current_phase(dt) == "ORB_FORMING"

    def test_orb_forming_mid(self):
        dt = datetime(2026, 4, 27, 9, 25)
        assert get_current_phase(dt) == "ORB_FORMING"

    def test_morning_active(self):
        dt = datetime(2026, 4, 27, 9, 30)
        assert get_current_phase(dt) == "MORNING_ACTIVE"

    def test_morning_active_mid(self):
        dt = datetime(2026, 4, 27, 10, 30)
        assert get_current_phase(dt) == "MORNING_ACTIVE"

    def test_caution_zone(self):
        dt = datetime(2026, 4, 27, 11, 30)
        assert get_current_phase(dt) == "CAUTION_ZONE"

    def test_afternoon(self):
        dt = datetime(2026, 4, 27, 13, 0)
        assert get_current_phase(dt) == "AFTERNOON"

    def test_closing(self):
        dt = datetime(2026, 4, 27, 14, 45)
        assert get_current_phase(dt) == "CLOSING"

    def test_done(self):
        dt = datetime(2026, 4, 27, 15, 15)
        assert get_current_phase(dt) == "DONE"

    def test_done_late(self):
        dt = datetime(2026, 4, 27, 16, 0)
        assert get_current_phase(dt) == "DONE"


class TestStrategyAttributes:
    def test_name(self):
        s = IntradayFuturesStrategy()
        assert s.name == StrategyName.INTRADAY_FUTURES

    def test_holding_type(self):
        s = IntradayFuturesStrategy()
        assert s.holding_type == "INTRADAY"

    def test_max_lots(self):
        s = IntradayFuturesStrategy()
        assert s.max_lots == 2


class TestGetSymbols:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_watchlist(self):
        s = IntradayFuturesStrategy()
        s._watchlist_cache = None
        s._watchlist_cache_ts = 0.0
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        with patch("app.core.redis.get_redis", return_value=mock_redis):
            result = await s.get_symbols()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_symbols_from_watchlist(self):
        import json

        s = IntradayFuturesStrategy()
        s._watchlist_cache = None
        s._watchlist_cache_ts = 0.0
        watchlist = [{"symbol": "TCS"}, {"symbol": "INFY"}]
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(watchlist))
        with patch("app.core.redis.get_redis", return_value=mock_redis):
            result = await s.get_symbols()
        assert result == ["TCS", "INFY"]

    @pytest.mark.asyncio
    async def test_uses_cache(self):
        import time

        s = IntradayFuturesStrategy()
        s._watchlist_cache = ["TCS"]
        s._watchlist_cache_ts = time.time()
        result = await s.get_symbols()
        assert result == ["TCS"]


class TestORBFormation:
    def test_orb_levels_tracked(self):
        s = IntradayFuturesStrategy()
        candles = [
            Candle(open=500, high=510, low=495, close=505, volume=100000),
            Candle(open=505, high=515, low=498, close=510, volume=120000),
            Candle(open=510, high=520, low=500, close=515, volume=110000),
        ]
        ctx = _make_ctx(candles_5m=candles)
        s._update_orb_levels(ctx)
        assert s._orb_levels["TCS"]["high"] == 520
        assert s._orb_levels["TCS"]["low"] == 495

    def test_returns_none_during_orb_forming(self):
        s = IntradayFuturesStrategy()
        ctx = _make_ctx()
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="ORB_FORMING",
        ):
            result = s.evaluate(ctx)
        assert result is None


class TestORBBreakout:
    # ORB range ~1.6% at price ~500 — within default min_orb_range_pct (0.4%)
    # and max_orb_range_pct (2.0%)
    def _setup_strategy_with_orb(self, orb_high=504, orb_low=496):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": orb_high, "low": orb_low}}
        return s

    def test_long_breakout(self):
        s = self._setup_strategy_with_orb()
        # 5m candle close=507 > orb_high=504 → LONG breakout
        # risk = 507 - 496 = 11, target = 507 + 11*1.5 = 523.5
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT
        assert signal.instrument_type == InstrumentType.FUTURE
        assert signal.stop_loss == 496
        assert signal.entry_price == 507
        assert signal.target_price == 507 + 11 * 1.5

    def test_short_breakdown(self):
        s = self._setup_strategy_with_orb()
        # 5m candle close=493 < orb_low=496 → SHORT breakdown
        # risk = 504 - 493 = 11, target = 493 - 11*1.5 = 476.5
        candles = [Candle(open=496, high=497, low=492, close=493, volume=150000)]
        ctx = _make_ctx(price=493, vwap_val=498, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_FUT
        assert signal.stop_loss == 504
        assert signal.entry_price == 493
        assert signal.target_price == 493 - 11 * 1.5

    def test_no_signal_inside_range(self):
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=498, high=502, low=497, close=500, volume=100000)]
        ctx = _make_ctx(price=500, vwap_val=500, candles_5m=candles)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_no_signal_during_closing(self):
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(price=507, vwap_val=502, candles_5m=candles)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="CLOSING",
        ):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_vwap_filter_blocks_long_below_vwap(self):
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(price=507, vwap_val=520, candles_5m=candles)  # price below VWAP
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_adr_filter_blocks_low_adr(self):
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        flat_candles = [
            Candle(open=500, high=500, low=500, close=500, volume=100000)
            for _ in range(25)
        ]
        ctx = _make_ctx(price=507, vwap_val=502, candles_5m=candles, candles_daily=flat_candles)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_price_filter_blocks_penny(self):
        # ORB at 59-61 (~3.3% range on ~60 price — would also be blocked by range filter)
        # Use range that passes range filter but fails price filter
        s = self._setup_strategy_with_orb(60.5, 59.5)
        candles = [Candle(open=60, high=61.5, low=60, close=61, volume=150000)]
        ctx = _make_ctx(price=61, vwap_val=55, candles_5m=candles)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_rr_check_blocks_zero_risk(self):
        # Zero ORB range → rejected by min_orb_range_pct filter
        s = self._setup_strategy_with_orb(500, 500)
        candles = [Candle(open=500, high=502, low=499, close=501, volume=150000)]
        ctx = _make_ctx(price=501, vwap_val=499, candles_5m=candles)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None  # blocked by ORB range validation

    def test_no_signal_without_orb_levels(self):
        s = IntradayFuturesStrategy()  # no _orb_levels set
        candles = [Candle(open=510, high=516, low=509, close=515, volume=150000)]
        ctx = _make_ctx(price=515, vwap_val=505, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ):
            signal = s.evaluate(ctx)
        assert signal is None


class TestPositionSizing:
    def _full_params(self):
        """Params that satisfy all 6 conditions for 2-lot sizing."""
        bias = MagicMock()
        bias.strength = "STRONG"
        return {
            "_nifty_bias": bias,
            "_screener_score": 80,
            "_briefing_approach": "aggressive",
            "_briefing_max_lots": 2,
            "_stock_trend_strength": "STRONG",
        }

    def _orb_indicators(self, enhanced=True):
        return {"setup_type": "ORB", "enhanced_orb": enhanced}

    def _non_orb_indicators(self, setup="VWAP_BOUNCE"):
        return {"setup_type": setup}

    def test_default_1_lot(self):
        s = IntradayFuturesStrategy()
        assert s._compute_lots(rvol=1.8, params={}) == 1

    def test_2_lots_all_conditions_met_orb(self):
        s = IntradayFuturesStrategy()
        result = s._compute_lots(rvol=3.5, params=self._full_params(), indicators=self._orb_indicators())
        assert result == 2

    def test_2_lots_non_orb_skips_enhanced(self):
        s = IntradayFuturesStrategy()
        result = s._compute_lots(rvol=3.5, params=self._full_params(), indicators=self._non_orb_indicators())
        assert result == 2

    def test_1_lot_no_rvol(self):
        s = IntradayFuturesStrategy()
        assert s._compute_lots(rvol=None, params=self._full_params(), indicators=self._orb_indicators()) == 1

    def test_1_lot_weak_bias(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_nifty_bias"].strength = "WEAK"
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._orb_indicators()) == 1

    def test_1_lot_low_screener_score(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_screener_score"] = 60
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._orb_indicators()) == 1

    def test_1_lot_not_enhanced_orb(self):
        s = IntradayFuturesStrategy()
        assert s._compute_lots(rvol=3.5, params=self._full_params(), indicators=self._orb_indicators(enhanced=False)) == 1

    def test_1_lot_briefing_not_aggressive(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_briefing_approach"] = "normal"
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._orb_indicators()) == 1

    def test_vix_cap_at_18(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_india_vix"] = 18.0
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._orb_indicators()) == 1

    def test_vix_below_18_allows_2(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_india_vix"] = 15.0
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._orb_indicators()) == 2

    def test_briefing_cap_respected(self):
        s = IntradayFuturesStrategy()
        params = self._full_params()
        params["_briefing_max_lots"] = 1
        assert s._compute_lots(rvol=3.5, params=params, indicators=self._non_orb_indicators()) == 1


class TestConfidence:
    def _max_params(self):
        bias = MagicMock()
        bias.score = 0.8  # strongly bullish — aligns with is_long=True default
        return {
            "_nifty_bias": bias,
            "_screener_score": 90,
            "rvol_threshold": 1.5,
            "_gap_direction": "UP",
            "_relative_gap_pct": 2.0,
            "_stock_trend_score": 0.5,
            "_oi_direction": "long_buildup",
            "_oi_change_pct": 20.0,
        }

    def _min_params(self):
        return {"rvol_threshold": 1.5}

    def test_all_factors_maxed(self):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000) for _ in range(10)]
        ctx = _make_ctx(candles_5m=candles, params=self._max_params())
        conf = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=3.0, breakout_vol=400000)
        assert conf >= 80

    def test_all_factors_minimum(self):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=100000)]
        ctx = _make_ctx(candles_5m=candles, params=self._min_params())
        conf = s._compute_confidence(ctx, "CAUTION_ZONE", ctx.strategy_params, "GAP_CONTINUATION", rvol=0.5, breakout_vol=1000)
        assert conf < 30

    def test_morning_higher_than_caution(self):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=100000) for _ in range(5)]
        ctx = _make_ctx(candles_5m=candles, params=self._max_params())
        morning = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=2.0)
        caution = s._compute_confidence(ctx, "CAUTION_ZONE", ctx.strategy_params, "ORB", rvol=2.0)
        assert morning > caution

    def test_deterministic(self):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=100000) for _ in range(5)]
        ctx = _make_ctx(candles_5m=candles, params=self._max_params())
        c1 = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=2.0)
        c2 = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=2.0)
        assert c1 == c2

    def test_higher_rvol_higher_confidence(self):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=100000) for _ in range(5)]
        ctx = _make_ctx(candles_5m=candles, params=self._max_params())
        low = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=1.6)
        high = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=3.0)
        assert high > low


class TestShouldExit:
    def test_always_returns_none(self):
        s = IntradayFuturesStrategy()
        ctx = _make_ctx()
        assert s.should_exit(ctx, 500, 490, 520) is None


def _make_prev_day(pdh=510, pdl=490, pdc=500, pdo=495):
    from app.core.enums import DayBias
    return PreviousDayLevels(
        pdh=pdh, pdl=pdl, pdc=pdc, pdo=pdo,
        day_range=pdh - pdl, bias=DayBias.NEUTRAL,
    )


class TestVWAPBounce:
    """Tests for _check_vwap_bounce sub-setup."""

    def _make_trend_candles(self, vwap, above=True, count=6):
        """Create candles consistently above or below VWAP with a reversal at end."""
        candles = []
        if above:
            for i in range(count - 1):
                c = Candle(open=vwap + 3 + i, high=vwap + 8 + i, low=vwap + 1, close=vwap + 5 + i, volume=80000)
                candles.append(c)
            # Last candle: bearish then bullish reversal (bullish engulfing)
            candles.append(Candle(open=vwap + 5, high=vwap + 3, low=vwap + 0.5, close=vwap + 1, volume=70000))
            candles.append(Candle(open=vwap + 0.5, high=vwap + 6, low=vwap + 0.3, close=vwap + 5, volume=120000))
        else:
            for i in range(count - 1):
                c = Candle(open=vwap - 3 - i, high=vwap - 1, low=vwap - 8 - i, close=vwap - 5 - i, volume=80000)
                candles.append(c)
            # Bearish reversal (bearish engulfing)
            candles.append(Candle(open=vwap - 5, high=vwap - 0.5, low=vwap - 1, close=vwap - 1, volume=70000))
            candles.append(Candle(open=vwap - 0.5, high=vwap - 0.3, low=vwap - 6, close=vwap - 5, volume=120000))
        return candles

    def test_bullish_bounce(self):
        s = IntradayFuturesStrategy()
        vwap = 500.0
        candles = self._make_trend_candles(vwap, above=True)
        # Price near VWAP (within 0.2%)
        price = vwap + 0.5  # 0.1% from VWAP
        ctx = _make_ctx(price=price, vwap_val=vwap, candles_5m=candles, atr_5m=3.0)
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_vwap_bounce(ctx, "MORNING_ACTIVE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT
        assert signal.indicators["setup_type"] == "VWAP_BOUNCE"

    def test_bearish_bounce(self):
        s = IntradayFuturesStrategy()
        vwap = 500.0
        candles = self._make_trend_candles(vwap, above=False)
        price = vwap - 0.5
        ctx = _make_ctx(price=price, vwap_val=vwap, candles_5m=candles, atr_5m=3.0)
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 11, 0)
            signal = s._check_vwap_bounce(ctx, "CAUTION_ZONE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_FUT

    def test_skip_before_10am(self):
        s = IntradayFuturesStrategy()
        vwap = 500.0
        candles = self._make_trend_candles(vwap, above=True)
        ctx = _make_ctx(price=vwap + 0.5, vwap_val=vwap, candles_5m=candles, atr_5m=3.0)
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 45)
            signal = s._check_vwap_bounce(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_mixed_candles(self):
        s = IntradayFuturesStrategy()
        vwap = 500.0
        # Mix of above and below VWAP
        candles = [
            Candle(open=503, high=508, low=501, close=505, volume=80000),
            Candle(open=497, high=501, low=495, close=498, volume=80000),
            Candle(open=503, high=507, low=501, close=504, volume=80000),
            Candle(open=496, high=500, low=494, close=497, volume=80000),
            Candle(open=503, high=506, low=501, close=505, volume=80000),
            Candle(open=496, high=500, low=493, close=497, volume=80000),
        ]
        ctx = _make_ctx(price=vwap + 0.5, vwap_val=vwap, candles_5m=candles, atr_5m=3.0)
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_vwap_bounce(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_no_reversal_candle(self):
        s = IntradayFuturesStrategy()
        vwap = 500.0
        # All candles above VWAP but no reversal pattern
        candles = [
            Candle(open=vwap + 3, high=vwap + 8, low=vwap + 1, close=vwap + 5, volume=80000)
            for _ in range(6)
        ]
        ctx = _make_ctx(price=vwap + 0.5, vwap_val=vwap, candles_5m=candles, atr_5m=3.0)
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_vwap_bounce(ctx, "MORNING_ACTIVE", {})
        assert signal is None


class TestPDHPDLBreakout:
    """Tests for _check_pdh_pdl_breakout sub-setup."""

    def _breakout_candles(self, close, volume=200000):
        """Multiple candles so avg volume is lower than the breakout candle."""
        base = [Candle(open=500, high=505, low=498, close=502, volume=80000) for _ in range(5)]
        base.append(Candle(open=close - 2, high=close + 1, low=close - 3, close=close, volume=volume))
        return base

    def test_long_breakout_above_pdh(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490, pdc=500)
        candles = self._breakout_candles(515, volume=200000)
        ctx = _make_ctx(
            price=515, vwap_val=512, candles_5m=candles,
            previous_day=prev, atr_5m=5.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_pdh_pdl_breakout(ctx, "MORNING_ACTIVE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT
        assert signal.indicators["setup_type"] == "PDH_PDL"

    def test_short_breakdown_below_pdl(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490, pdc=500)
        candles = self._breakout_candles(485, volume=200000)
        ctx = _make_ctx(
            price=485, vwap_val=488, candles_5m=candles,
            previous_day=prev, atr_5m=5.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_pdh_pdl_breakout(ctx, "MORNING_ACTIVE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_FUT

    def test_skip_after_2pm(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490)
        candles = [Candle(open=508, high=516, low=507, close=515, volume=200000)]
        ctx = _make_ctx(
            price=515, vwap_val=512, candles_5m=candles,
            previous_day=prev, atr_5m=5.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 14, 15)
            signal = s._check_pdh_pdl_breakout(ctx, "AFTERNOON", {})
        assert signal is None

    def test_skip_low_volume(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490)
        # Low volume breakout candle
        candles = [Candle(open=508, high=516, low=507, close=515, volume=50000)]
        ctx = _make_ctx(
            price=515, vwap_val=512, candles_5m=candles,
            previous_day=prev, atr_5m=5.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_pdh_pdl_breakout(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_vwap_misaligned(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490)
        candles = [Candle(open=508, high=516, low=507, close=515, volume=200000)]
        # Long breakout but price below VWAP
        ctx = _make_ctx(
            price=515, vwap_val=520, candles_5m=candles,
            previous_day=prev, atr_5m=5.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 10, 30)
            signal = s._check_pdh_pdl_breakout(ctx, "MORNING_ACTIVE", {})
        assert signal is None


class TestGapContinuation:
    """Tests for _check_gap_continuation sub-setup."""

    def test_gap_up_continuation(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490, pdc=500)
        today_open = 506.0  # 1.2% gap up
        candles = [
            Candle(open=506, high=510, low=505, close=508, volume=300000),
            Candle(open=508, high=512, low=507, close=510, volume=280000),
            Candle(open=510, high=515, low=509, close=513, volume=260000),
            Candle(open=513, high=518, low=512, close=516, volume=200000),
        ]
        ctx = _make_ctx(
            price=516, candles_5m=candles, previous_day=prev,
            today_open=today_open, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 50)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT
        assert signal.indicators["setup_type"] == "GAP_CONTINUATION"
        assert signal.indicators["gap_direction"] == "UP"

    def test_gap_down_continuation(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdh=510, pdl=490, pdc=500)
        today_open = 494.0  # 1.2% gap down
        candles = [
            Candle(open=494, high=496, low=490, close=492, volume=300000),
            Candle(open=492, high=494, low=488, close=490, volume=280000),
            Candle(open=490, high=492, low=486, close=488, volume=260000),
            Candle(open=488, high=490, low=484, close=485, volume=200000),
        ]
        ctx = _make_ctx(
            price=485, candles_5m=candles, previous_day=prev,
            today_open=today_open, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 50)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_FUT
        assert signal.indicators["gap_direction"] == "DOWN"

    def test_skip_after_11am(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdc=500)
        candles = [
            Candle(open=506, high=510, low=505, close=508, volume=300000),
            Candle(open=508, high=512, low=507, close=510, volume=280000),
            Candle(open=510, high=515, low=509, close=513, volume=260000),
            Candle(open=513, high=518, low=512, close=516, volume=200000),
        ]
        ctx = _make_ctx(
            price=516, candles_5m=candles, previous_day=prev,
            today_open=506.0, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 11, 15)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_small_gap(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdc=500)
        today_open = 501.0  # 0.2% gap — below default 0.5% threshold
        candles = [
            Candle(open=501, high=503, low=500, close=502, volume=300000),
            Candle(open=502, high=504, low=501, close=503, volume=280000),
            Candle(open=503, high=505, low=502, close=504, volume=260000),
            Candle(open=504, high=506, low=503, close=505, volume=200000),
        ]
        ctx = _make_ctx(
            price=505, candles_5m=candles, previous_day=prev,
            today_open=today_open, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 50)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_gap_filled(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdc=500)
        today_open = 506.0
        candles = [
            Candle(open=506, high=510, low=505, close=508, volume=300000),
            Candle(open=508, high=512, low=507, close=510, volume=280000),
            Candle(open=510, high=515, low=509, close=513, volume=260000),
            Candle(open=513, high=514, low=498, close=499, volume=200000),  # fills gap
        ]
        ctx = _make_ctx(
            price=499, candles_5m=candles, previous_day=prev,
            today_open=today_open, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 50)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is None

    def test_skip_low_opening_volume(self):
        s = IntradayFuturesStrategy()
        prev = _make_prev_day(pdc=500)
        today_open = 506.0
        # Very low volume in first 3 candles
        candles = [
            Candle(open=506, high=510, low=505, close=508, volume=10000),
            Candle(open=508, high=512, low=507, close=510, volume=10000),
            Candle(open=510, high=515, low=509, close=513, volume=10000),
            Candle(open=513, high=518, low=512, close=516, volume=200000),
        ]
        ctx = _make_ctx(
            price=516, candles_5m=candles, previous_day=prev,
            today_open=today_open, atr_5m=4.0, volume_avg_20d=7_500_000,
        )
        with patch("app.core.utils.now_ist") as m:
            m.return_value = datetime(2026, 4, 28, 9, 50)
            signal = s._check_gap_continuation(ctx, "MORNING_ACTIVE", {})
        assert signal is None


class TestPhaseDispatch:
    """Tests for phase-based dispatch ordering and time guards."""

    def test_morning_dispatch_order(self):
        s = IntradayFuturesStrategy()
        assert s._get_dispatch_order("MORNING_ACTIVE") == ["ORB", "GAP_CONTINUATION", "PDH_PDL", "VWAP_BOUNCE"]

    def test_caution_zone_dispatch_order(self):
        s = IntradayFuturesStrategy()
        assert s._get_dispatch_order("CAUTION_ZONE") == ["PDH_PDL", "VWAP_BOUNCE"]

    def test_afternoon_dispatch_order(self):
        s = IntradayFuturesStrategy()
        assert s._get_dispatch_order("AFTERNOON") == ["VWAP_BOUNCE", "PDH_PDL"]

    def test_disabled_setup_skipped(self):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": 510, "low": 490}}
        candles = [Candle(open=510, high=516, low=509, close=515, volume=150000)]
        ctx = _make_ctx(price=515, vwap_val=505, candles_5m=candles,
                        volume_avg_20d=7_500_000, params={"enabled_setups": ["VWAP_BOUNCE"]})
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_orb_skipped_after_11am(self):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": 510, "low": 490}}
        candles = [Candle(open=510, high=516, low=509, close=515, volume=150000)]
        ctx = _make_ctx(price=515, vwap_val=505, candles_5m=candles,
                        volume_avg_20d=7_500_000, params={"enabled_setups": ["ORB"]})
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 11, 15)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_phase_transition_logged(self):
        s = IntradayFuturesStrategy()
        s._last_logged_phase = None
        ctx = _make_ctx()
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            s.evaluate(ctx)
        logs = s.drain_pending_logs()
        assert any(cat == "PHASE" for cat, _ in logs)


class TestCautionZoneConfirmation:
    """Tests for caution zone pending confirmation logic."""

    def _make_signal(self, symbol="TCS"):
        return StrategySignal(
            strategy_name=StrategyName.INTRADAY_FUTURES,
            symbol=symbol,
            signal_type=SignalType.BUY_FUT,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date(2026, 4, 28),
            entry_price=515,
            stop_loss=510,
            target_price=522,
            confidence=70.0,
            reason="test",
            indicators={"setup_type": "PDH_PDL"},
        )

    def test_stores_pending_in_caution_zone(self):
        s = IntradayFuturesStrategy()
        ctx = _make_ctx(price=515, candles_5m=[
            Candle(open=510, high=516, low=509, close=515, volume=200000)
        ])
        signal = self._make_signal()
        s._store_pending_confirmation(ctx, signal, "PDH_PDL")
        assert "TCS" in s._pending_confirmations
        assert s._pending_confirmations["TCS"]["setup_type"] == "PDH_PDL"

    def test_confirms_on_next_candle_if_held(self):
        s = IntradayFuturesStrategy()
        signal = self._make_signal()
        s._pending_confirmations["TCS"] = {
            "setup_type": "PDH_PDL",
            "signal": signal,
            "direction": "LONG",
            "breakout_price": 515,
            "stored_at": time.time(),
            "candle_count": 5,
        }
        # 6 candles now (was 5 when stored), latest close >= breakout price
        candles = [Candle(open=515, high=520, low=514, close=518, volume=100000)] * 6
        ctx = _make_ctx(price=518, candles_5m=candles)
        result = s._check_pending_confirmations(ctx, "CAUTION_ZONE")
        assert result is not None
        assert result.signal_type == SignalType.BUY_FUT

    def test_discards_if_price_not_held(self):
        s = IntradayFuturesStrategy()
        signal = self._make_signal()
        s._pending_confirmations["TCS"] = {
            "setup_type": "PDH_PDL",
            "signal": signal,
            "direction": "LONG",
            "breakout_price": 515,
            "stored_at": time.time(),
            "candle_count": 5,
        }
        candles = [Candle(open=512, high=514, low=510, close=511, volume=100000)] * 6
        ctx = _make_ctx(price=511, candles_5m=candles)
        result = s._check_pending_confirmations(ctx, "CAUTION_ZONE")
        assert result is None
        assert "TCS" not in s._pending_confirmations

    def test_discards_after_10_minutes(self):
        s = IntradayFuturesStrategy()
        signal = self._make_signal()
        s._pending_confirmations["TCS"] = {
            "setup_type": "PDH_PDL",
            "signal": signal,
            "direction": "LONG",
            "breakout_price": 515,
            "stored_at": time.time() - 700,  # 11+ minutes ago
            "candle_count": 5,
        }
        candles = [Candle(open=515, high=520, low=514, close=518, volume=100000)] * 6
        ctx = _make_ctx(price=518, candles_5m=candles)
        result = s._check_pending_confirmations(ctx, "CAUTION_ZONE")
        assert result is None

    def test_discards_on_phase_change(self):
        s = IntradayFuturesStrategy()
        signal = self._make_signal()
        s._pending_confirmations["TCS"] = {
            "setup_type": "PDH_PDL",
            "signal": signal,
            "direction": "LONG",
            "breakout_price": 515,
            "stored_at": time.time(),
            "candle_count": 5,
        }
        candles = [Candle(open=515, high=520, low=514, close=518, volume=100000)] * 6
        ctx = _make_ctx(price=518, candles_5m=candles)
        result = s._check_pending_confirmations(ctx, "AFTERNOON")
        assert result is None
        assert "TCS" not in s._pending_confirmations


class TestStockTrendFilter:
    """Tests for stock trend direction filtering across sub-setups."""

    def _setup_strategy_with_orb(self, orb_high=504, orb_low=496):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": orb_high, "low": orb_low}}
        return s

    def test_strong_bearish_blocks_long(self):
        """STRONG BEARISH trend should block a LONG ORB breakout."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(
            price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000,
            params={"_stock_trend_strength": "STRONG", "_stock_bias": "BEARISH"},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_strong_bullish_blocks_short(self):
        """STRONG BULLISH trend should block a SHORT ORB breakdown."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=496, high=497, low=492, close=493, volume=150000)]
        ctx = _make_ctx(
            price=493, vwap_val=498, candles_5m=candles, volume_avg_20d=7_500_000,
            params={"_stock_trend_strength": "STRONG", "_stock_bias": "BULLISH"},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_moderate_allows_with_risk_warning(self):
        """MODERATE opposing trend should allow signal but add risk_warning."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(
            price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000,
            params={"_stock_trend_strength": "MODERATE", "_stock_bias": "BEARISH"},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert any("MODERATE" in w for w in signal.indicators.get("risk_warnings", []))

    def test_neutral_does_not_filter(self):
        """NEUTRAL trend should not filter any direction."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(
            price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000,
            params={"_stock_trend_strength": "WEAK", "_stock_bias": "NEUTRAL"},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None

    def test_no_trend_data_does_not_filter(self):
        """Missing trend data should not block signals."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(
            price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000,
            params={},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None

    def test_strong_trend_aligned_allows(self):
        """STRONG BULLISH trend should allow LONG signals."""
        s = self._setup_strategy_with_orb()
        candles = [Candle(open=504, high=508, low=503, close=507, volume=150000)]
        ctx = _make_ctx(
            price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000,
            params={"_stock_trend_strength": "STRONG", "_stock_bias": "BULLISH"},
        )
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT


class TestStockTrendConfidence:
    """Tests for trend alignment factor in confidence scoring."""

    def _base_params(self):
        bias = MagicMock()
        bias.score = 0.8
        return {
            "_nifty_bias": bias,
            "_screener_score": 90,
            "rvol_threshold": 1.5,
        }

    def test_trend_aligned_boosts_confidence(self):
        """Bullish trend + LONG should produce higher confidence than no trend."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000) for _ in range(10)]

        params_with_trend = {**self._base_params(), "_stock_trend_score": 0.7}
        ctx_with = _make_ctx(candles_5m=candles, params=params_with_trend)
        conf_with = s._compute_confidence(ctx_with, "MORNING_ACTIVE", ctx_with.strategy_params, "ORB", rvol=3.0, is_long=True)

        params_no_trend = self._base_params()
        ctx_without = _make_ctx(candles_5m=candles, params=params_no_trend)
        conf_without = s._compute_confidence(ctx_without, "MORNING_ACTIVE", ctx_without.strategy_params, "ORB", rvol=3.0, is_long=True)

        assert conf_with > conf_without

    def test_trend_opposing_lowers_confidence(self):
        """Bearish trend + LONG should produce lower confidence than no trend."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000) for _ in range(10)]

        params_opposing = {**self._base_params(), "_stock_trend_score": -0.7}
        ctx_opposing = _make_ctx(candles_5m=candles, params=params_opposing)
        conf_opposing = s._compute_confidence(ctx_opposing, "MORNING_ACTIVE", ctx_opposing.strategy_params, "ORB", rvol=3.0, is_long=True)

        params_neutral = self._base_params()
        ctx_neutral = _make_ctx(candles_5m=candles, params=params_neutral)
        conf_neutral = s._compute_confidence(ctx_neutral, "MORNING_ACTIVE", ctx_neutral.strategy_params, "ORB", rvol=3.0, is_long=True)

        assert conf_opposing < conf_neutral

    def test_trend_factor_clamped(self):
        """Extreme trend scores should not push factor outside [0, 1]."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000) for _ in range(10)]

        params = {**self._base_params(), "_stock_trend_score": 1.0}
        ctx = _make_ctx(candles_5m=candles, params=params)
        conf = s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB", rvol=3.0, is_long=True)
        assert 0 <= conf <= 100


# ── Fix-specific tests: ORB 5-min candle close confirmation ─────────────

class TestORB5mCandleClose:
    """ORB breakout uses 5m candle close, not ctx.current_price."""

    def _setup(self, orb_high=504, orb_low=496):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": orb_high, "low": orb_low}}
        return s

    def test_live_tick_above_but_candle_close_inside_no_signal(self):
        """Live tick (current_price) above ORB high, but 5m candle close is inside range — no signal."""
        s = self._setup()
        candles = [Candle(open=500, high=507, low=499, close=502, volume=150000)]
        ctx = _make_ctx(price=507, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_candle_close_above_orb_high_signals(self):
        """5m candle close above ORB high → signal, even if live price retraced."""
        s = self._setup()
        candles = [Candle(open=503, high=509, low=502, close=506, volume=150000)]
        ctx = _make_ctx(price=505, vwap_val=502, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT
        assert signal.entry_price == 506  # candle close, not ctx.current_price

    def test_entry_price_is_candle_close_not_live_tick(self):
        """Entry price must equal the confirmed 5m candle close."""
        s = self._setup()
        candles = [Candle(open=497, high=498, low=490, close=493, volume=150000)]
        ctx = _make_ctx(price=491, vwap_val=498, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.entry_price == 493  # candle close
        assert signal.entry_price != 491  # not live tick

    def test_no_candles_5m_returns_none(self):
        """No 5m candles available → cannot confirm breakout."""
        s = self._setup()
        ctx = _make_ctx(price=507, vwap_val=502, candles_5m=[], volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None


# ── Fix-specific tests: ORB range validation ────────────────────────────

class TestORBRangeValidation:
    """ORB range filter rejects ranges below min_orb_range_pct or above max_orb_range_pct."""

    def _setup(self, orb_high, orb_low):
        s = IntradayFuturesStrategy()
        s._orb_levels = {"TCS": {"high": orb_high, "low": orb_low}}
        return s

    def test_range_too_narrow_rejected(self):
        """ORB range 0.2% < min 0.4% → no signal."""
        # 501/500 on a ~501 price = 0.2%
        s = self._setup(501, 500)
        candles = [Candle(open=501, high=503, low=500, close=502, volume=150000)]
        ctx = _make_ctx(price=502, vwap_val=499, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_range_too_wide_rejected(self):
        """ORB range 4% > max 2% → no signal."""
        # 520/500 on ~525 price = 3.8%
        s = self._setup(520, 500)
        candles = [Candle(open=520, high=530, low=519, close=525, volume=150000)]
        ctx = _make_ctx(price=525, vwap_val=515, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is None

    def test_range_within_bounds_passes(self):
        """ORB range comfortably within min/max bounds should not be blocked by range filter."""
        # 503/497 on ~504 price = ~1.19% — well within 0.4%-2.0%
        s = self._setup(503, 497)
        candles = [Candle(open=503, high=506, low=502, close=505, volume=150000)]
        ctx = _make_ctx(price=505, vwap_val=501, candles_5m=candles, volume_avg_20d=7_500_000)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT

    def test_custom_range_params_respected(self):
        """Custom min/max range from strategy params overrides defaults."""
        # 506/494 = 2.4% range on ~507 — normally blocked by default max (2.0%)
        s = self._setup(506, 494)
        candles = [Candle(open=506, high=510, low=505, close=508, volume=150000)]
        params = {"max_orb_range_pct": 3.0, "min_orb_range_pct": 0.2}
        ctx = _make_ctx(price=508, vwap_val=502, candles_5m=candles,
                        volume_avg_20d=7_500_000, params=params)
        with patch(
            "app.strategies.strategy_5_intraday_futures.get_current_phase",
            return_value="MORNING_ACTIVE",
        ), patch("app.core.utils.now_ist", return_value=datetime(2026, 4, 28, 9, 45)):
            signal = s.evaluate(ctx)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY_FUT


# ── Fix-specific tests: Direction-aware Nifty bias confidence factor ────

class TestBiasDirectionAwareConfidence:
    """Nifty bias confidence factor uses score-based direction alignment."""

    def _base_params(self, bias_score):
        bias = MagicMock()
        bias.score = bias_score
        return {
            "_nifty_bias": bias,
            "_screener_score": 80,
            "rvol_threshold": 1.5,
        }

    def test_bullish_bias_helps_long(self):
        """Positive bias score should give higher bias_factor for LONG."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = self._base_params(0.8)
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=True, indicators=indicators)
        assert indicators["confidence_factors"]["bias_factor"] == round((0.8 + 1.0) / 2.0, 3)

    def test_bullish_bias_hurts_short(self):
        """Positive bias score should give lower bias_factor for SHORT."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = self._base_params(0.8)
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=False, indicators=indicators)
        # alignment = -0.8 for SHORT → factor = (-0.8 + 1.0) / 2.0 = 0.1
        assert indicators["confidence_factors"]["bias_factor"] == round((-0.8 + 1.0) / 2.0, 3)

    def test_bearish_bias_helps_short(self):
        """Negative bias score should give higher bias_factor for SHORT."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = self._base_params(-0.7)
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=False, indicators=indicators)
        # alignment = -(-0.7) = 0.7 for SHORT → factor = (0.7 + 1.0) / 2.0 = 0.85
        assert indicators["confidence_factors"]["bias_factor"] == round((0.7 + 1.0) / 2.0, 3)

    def test_neutral_bias_gives_half(self):
        """Zero bias score → factor = 0.5 for either direction."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = self._base_params(0.0)
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=True, indicators=indicators)
        assert indicators["confidence_factors"]["bias_factor"] == 0.5

    def test_missing_bias_gives_zero(self):
        """No bias object → factor = 0.0 (missing data penalty)."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = {"_screener_score": 80, "rvol_threshold": 1.5}
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=True, indicators=indicators)
        assert indicators["confidence_factors"]["bias_factor"] == 0.0


# ── Fix-specific tests: 4-way OI direction confidence factor ────────────

class TestOIDirectionAwareConfidence:
    """OI confidence factor uses 4-way classification: long_buildup, short_buildup,
    short_covering, long_unwinding — direction-aware scoring."""

    def _base_params(self, oi_direction, oi_change_pct=5.0):
        bias = MagicMock()
        bias.score = 0.5
        return {
            "_nifty_bias": bias,
            "_screener_score": 80,
            "rvol_threshold": 1.5,
            "_oi_direction": oi_direction,
            "_oi_change_pct": oi_change_pct,
        }

    def _get_oi_factor(self, oi_direction, is_long, oi_change_pct=5.0):
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        params = self._base_params(oi_direction, oi_change_pct)
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=is_long, indicators=indicators)
        return indicators["confidence_factors"]["oi_factor"]

    def test_long_buildup_helps_long(self):
        factor = self._get_oi_factor("long_buildup", is_long=True)
        assert factor > 0.6

    def test_long_buildup_hurts_short(self):
        factor = self._get_oi_factor("long_buildup", is_long=False)
        assert factor < 0.5

    def test_short_buildup_helps_short(self):
        factor = self._get_oi_factor("short_buildup", is_long=False)
        assert factor > 0.6

    def test_short_buildup_hurts_long(self):
        factor = self._get_oi_factor("short_buildup", is_long=True)
        assert factor < 0.5

    def test_short_covering_mildly_bullish(self):
        factor_long = self._get_oi_factor("short_covering", is_long=True)
        factor_short = self._get_oi_factor("short_covering", is_long=False)
        assert factor_long == 0.6
        assert factor_short == 0.4

    def test_long_unwinding_mildly_bearish(self):
        factor_long = self._get_oi_factor("long_unwinding", is_long=True)
        factor_short = self._get_oi_factor("long_unwinding", is_long=False)
        assert factor_long == 0.4
        assert factor_short == 0.6

    def test_flat_is_neutral(self):
        factor = self._get_oi_factor("flat", is_long=True)
        assert factor == 0.5

    def test_missing_oi_penalised(self):
        """No OI data → factor = 0.2 (missing data penalty, not neutral 0.5)."""
        s = IntradayFuturesStrategy()
        candles = [Candle(open=500, high=510, low=495, close=505, volume=200000)] * 10
        indicators: dict = {}
        bias = MagicMock()
        bias.score = 0.5
        params = {"_nifty_bias": bias, "_screener_score": 80, "rvol_threshold": 1.5}
        ctx = _make_ctx(candles_5m=candles, params=params)
        s._compute_confidence(ctx, "MORNING_ACTIVE", ctx.strategy_params, "ORB",
                              rvol=3.0, is_long=True, indicators=indicators)
        assert indicators["confidence_factors"]["oi_factor"] == 0.2

    def test_higher_oi_change_boosts_buildup_factor(self):
        """Larger OI change % should increase the raw factor for buildup types."""
        small = self._get_oi_factor("long_buildup", is_long=True, oi_change_pct=2.0)
        large = self._get_oi_factor("long_buildup", is_long=True, oi_change_pct=15.0)
        assert large > small
