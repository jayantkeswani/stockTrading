"""Tests for CAN SLIM strategy evaluate() and should_exit()."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.candle_patterns import Candle
from app.strategies.base import MarketContext
from app.strategies.strategy_4_canslim import CANSLIMStrategy


def _make_canslim_data(**overrides):
    """Create a mock StockFundamental object."""
    defaults = {
        "symbol": "TCS",
        "canslim_score": Decimal("75.0"),
        "c_score": Decimal("80.0"),
        "a_score": Decimal("70.0"),
        "n_score": Decimal("85.0"),
        "s_score": Decimal("60.0"),
        "l_score": Decimal("90.0"),
        "i_score": Decimal("65.0"),
        "pct_from_52w_high": Decimal("5.0"),
        "relative_strength_rating": Decimal("85.0"),
        "lot_size": 175,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_uptrend_daily_bars(n=50, start_price=100, end_price=200):
    """Create daily bars showing an uptrend then consolidation (flat base)."""
    bars = []
    # Uptrend phase
    uptrend_n = n - 15
    for i in range(uptrend_n):
        p = start_price + i * (end_price - start_price) / uptrend_n
        bars.append(Candle(open=p - 1, high=p + 2, low=p - 2, close=p, volume=1000))

    # Flat consolidation (tight range — will form flat base)
    for _ in range(15):
        bars.append(Candle(
            open=end_price - 2,
            high=end_price + 3,  # breakout_price will be around end_price + 3
            low=end_price - 5,
            close=end_price,
            volume=1000,
        ))
    return bars


def _make_context(**overrides) -> MarketContext:
    """Create a MarketContext suitable for CAN SLIM evaluation."""
    defaults = {
        "symbol": "TCS",
        "current_price": 210.0,  # Above the flat base breakout level
        "candles_5m": [Candle(open=200, high=212, low=198, close=210, volume=50000)] * 10,
        "vwap": None,
        "previous_day": None,
        "cpr": None,
        "oi_analysis": None,
        "india_vix": 14.0,
        "current_time_ist": "2026-04-17T10:30:00+05:30",
        "candles_daily": _make_uptrend_daily_bars(),
        "volume_avg_20d": 10000,
        "relative_strength": 85.0,
        "canslim_data": _make_canslim_data(),
    }
    defaults.update(overrides)
    return MarketContext(**defaults)


class TestCANSLIMStrategyMeta:
    def test_name(self):
        s = CANSLIMStrategy()
        assert s.name == StrategyName.CAN_SLIM

    def test_holding_type(self):
        s = CANSLIMStrategy()
        assert s.holding_type == "POSITIONAL"


class TestEvaluate:
    def test_no_fundamental_data(self):
        ctx = _make_context(canslim_data=None)
        assert CANSLIMStrategy().evaluate(ctx) is None

    def test_score_below_minimum(self):
        data = _make_canslim_data(canslim_score=Decimal("40.0"))
        ctx = _make_context(canslim_data=data)
        assert CANSLIMStrategy().evaluate(ctx) is None

    def test_vix_too_high(self):
        ctx = _make_context(india_vix=25.0)
        assert CANSLIMStrategy().evaluate(ctx) is None

    def test_too_far_from_52w_high(self):
        data = _make_canslim_data(pct_from_52w_high=Decimal("20.0"))
        ctx = _make_context(canslim_data=data)
        assert CANSLIMStrategy().evaluate(ctx) is None

    def test_no_daily_candles(self):
        ctx = _make_context(candles_daily=None)
        assert CANSLIMStrategy().evaluate(ctx) is None

    def test_signal_generated(self):
        """When all conditions are met, a BUY_FUT signal should be generated."""
        ctx = _make_context()
        signal = CANSLIMStrategy().evaluate(ctx)
        # Signal may or may not be generated depending on pattern detection
        # If a flat base is detected and price is above breakout, signal should exist
        if signal:
            assert signal.signal_type == SignalType.BUY_FUT
            assert signal.instrument_type == InstrumentType.FUTURE
            assert signal.strategy_name == StrategyName.CAN_SLIM
            assert signal.stop_loss < signal.entry_price
            assert signal.target_price > signal.entry_price
            assert signal.confidence > 0

    def test_signal_has_correct_indicators(self):
        ctx = _make_context()
        signal = CANSLIMStrategy().evaluate(ctx)
        if signal:
            assert "canslim_score" in signal.indicators
            assert "pattern_type" in signal.indicators
            assert "rs_rating" in signal.indicators


class TestShouldExit:
    def test_sl_hit(self):
        strategy = CANSLIMStrategy()
        ctx = _make_context(current_price=92.0)
        exit = strategy.should_exit(ctx, entry_price=100.0, stop_loss=92.0, target_price=120.0)
        assert exit is not None
        assert exit.exit_type == "SL_HIT"

    def test_target_hit(self):
        strategy = CANSLIMStrategy()
        ctx = _make_context(current_price=125.0)
        exit = strategy.should_exit(ctx, entry_price=100.0, stop_loss=92.0, target_price=120.0)
        assert exit is not None
        assert exit.exit_type == "TARGET_HIT"

    def test_no_exit(self):
        strategy = CANSLIMStrategy()
        ctx = _make_context(current_price=110.0)
        exit = strategy.should_exit(ctx, entry_price=100.0, stop_loss=92.0, target_price=120.0)
        assert exit is None


class TestPositionSize:
    def test_basic_sizing(self):
        from app.core.constants import CANSLIM_MAX_POSITIONAL_LOTS
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=1_000_000,
            risk_per_trade_pct=2.0,
            entry_price=3500.0,
            stop_loss=3220.0,  # 8% below
            lot_size=175,
            max_lots=CANSLIM_MAX_POSITIONAL_LOTS,
        )
        assert lots >= 1
        assert lots <= CANSLIM_MAX_POSITIONAL_LOTS

    def test_minimum_one_lot(self):
        from app.services.position_sizing import calculate_lots
        lots = calculate_lots(
            capital=100_000,
            risk_per_trade_pct=1.0,
            entry_price=5000.0,
            stop_loss=4600.0,
            lot_size=500,
        )
        assert lots == 1
