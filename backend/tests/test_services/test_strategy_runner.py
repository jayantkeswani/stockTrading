"""Tests for strategy_runner — auto strategy filtering and manual evaluation."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.strategies.base import MarketContext, StrategySignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    symbol: str = "NIFTY",
    signal_type: SignalType = SignalType.BUY_CE,
    confidence: float = 75.0,
) -> StrategySignal:
    """Build a minimal StrategySignal for testing."""
    return StrategySignal(
        strategy_name=StrategyName.VWAP_PULLBACK,
        symbol=symbol,
        signal_type=signal_type,
        instrument_type=InstrumentType.OPTION,
        strike_price=24000.0,
        expiry_date=date(2026, 4, 22),
        entry_price=100.0,
        stop_loss=70.0,
        target_price=145.0,
        confidence=confidence,
        reason="Test signal",
        indicators={"sl_pct": 0.30, "rr_multiplier": 1.5},
    )


def _make_strategy_config_row(name: str, symbols: list[str]):
    """Simulate a DB row from (strategy_name, symbols) query."""
    return (name, symbols)


# ---------------------------------------------------------------------------
# Tests for get_auto_strategies_for_symbol
# ---------------------------------------------------------------------------


class TestGetAutoStrategiesForSymbol:
    """Tests for the module-level function that queries which strategies
    have auto_mode=True for a given symbol."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_returns_matching_strategies(self, mock_session_factory):
        """When a strategy has auto_mode=True and includes the symbol, return it."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            _make_strategy_config_row("vwap_pullback", ["NIFTY", "BANKNIFTY"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("NIFTY")
        assert result == [StrategyName.VWAP_PULLBACK]

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_returns_empty_when_symbol_not_configured(self, mock_session_factory):
        """When the symbol isn't in any strategy's symbols list, return empty."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            _make_strategy_config_row("vwap_pullback", ["NIFTY", "BANKNIFTY"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("SENSEX")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_returns_empty_when_no_auto_strategies(self, mock_session_factory):
        """When no strategies have auto_mode=True, return empty."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("NIFTY")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_multiple_strategies_matching(self, mock_session_factory):
        """When multiple strategies have auto_mode for the same symbol."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            _make_strategy_config_row("vwap_pullback", ["NIFTY"]),
            _make_strategy_config_row("orb", ["NIFTY", "BANKNIFTY"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("NIFTY")
        assert StrategyName.VWAP_PULLBACK in result
        assert StrategyName.ORB in result
        assert len(result) == 2

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_skips_unknown_strategy_names(self, mock_session_factory):
        """Unknown strategy names in DB should be silently skipped."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            _make_strategy_config_row("nonexistent_strategy", ["NIFTY"]),
            _make_strategy_config_row("vwap_pullback", ["NIFTY"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("NIFTY")
        assert result == [StrategyName.VWAP_PULLBACK]

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_handles_none_symbols_list(self, mock_session_factory):
        """If symbols is None in DB row, don't crash."""
        from app.services.strategy_runner import get_auto_strategies_for_symbol

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            _make_strategy_config_row("vwap_pullback", None),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await get_auto_strategies_for_symbol("NIFTY")
        assert result == []


# ---------------------------------------------------------------------------
# Tests for evaluate_manual
# ---------------------------------------------------------------------------


class TestEvaluateManual:
    """Tests for the manual (API-driven) strategy evaluation path."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._handle_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.get_trading_config", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.get_strategy_params", new_callable=AsyncMock, return_value={})
    @patch("app.services.strategy_runner.strategy_runner._check_global_risk_limits", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._build_market_context", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._load_todays_candles", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._get_current_price", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._resolve_option", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._enrich_signal_snapshot", new_callable=AsyncMock)
    @patch("app.strategies.registry.get_strategy")
    async def test_generates_signal_on_manual_scan(
        self, mock_get_strategy, mock_enrich, mock_resolve_option, mock_price, mock_candles, mock_ctx, mock_risk, mock_params, mock_trading_cfg, mock_handle
    ):
        """Manual evaluation should call strategy.evaluate and handle the signal."""
        from app.services.strategy_runner import strategy_runner

        _cfg = MagicMock()
        _cfg.min_confidence_for_execution = 60.0
        mock_trading_cfg.return_value = _cfg
        mock_price.return_value = 24000.0
        mock_candles.return_value = []
        mock_risk.return_value = (True, None)

        mock_context = MagicMock(spec=MarketContext)
        mock_context.current_price = 24000.0
        mock_context.india_vix = 16.0
        mock_ctx.return_value = mock_context

        signal = _make_signal()
        mock_strategy = MagicMock()
        mock_strategy.evaluate.return_value = signal
        mock_get_strategy.return_value = mock_strategy
        mock_resolve_option.return_value = (signal, True, None)

        result = await strategy_runner.evaluate_manual("NIFTY", StrategyName.VWAP_PULLBACK)

        assert result is not None
        assert result.symbol == "NIFTY"
        mock_handle.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._get_current_price", new_callable=AsyncMock)
    async def test_returns_none_when_no_price(self, mock_price):
        """If no price is available, manual evaluation should return None."""
        from app.services.strategy_runner import strategy_runner

        mock_price.return_value = None

        result = await strategy_runner.evaluate_manual("NIFTY", StrategyName.VWAP_PULLBACK)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.get_strategy_params", new_callable=AsyncMock, return_value={})
    @patch("app.services.strategy_runner.strategy_runner._check_global_risk_limits", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._build_market_context", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._load_todays_candles", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._get_current_price", new_callable=AsyncMock)
    @patch("app.strategies.registry.get_strategy")
    async def test_returns_none_when_strategy_not_found(
        self, mock_get_strategy, mock_price, mock_candles, mock_ctx, mock_risk, mock_params
    ):
        """If the strategy doesn't exist in the registry, return None."""
        from app.services.strategy_runner import strategy_runner

        mock_price.return_value = 24000.0
        mock_candles.return_value = []
        mock_risk.return_value = (True, None)
        mock_ctx.return_value = MagicMock(spec=MarketContext)
        mock_get_strategy.return_value = None

        result = await strategy_runner.evaluate_manual("NIFTY", StrategyName.VWAP_PULLBACK)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._handle_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.get_strategy_params", new_callable=AsyncMock, return_value={})
    @patch("app.services.strategy_runner.strategy_runner._check_global_risk_limits", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._build_market_context", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._load_todays_candles", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._get_current_price", new_callable=AsyncMock)
    @patch("app.strategies.registry.get_strategy")
    async def test_returns_none_when_no_signal(
        self, mock_get_strategy, mock_price, mock_candles, mock_ctx, mock_risk, mock_params, mock_handle
    ):
        """When the strategy returns None (no signal), evaluate_manual returns None."""
        from app.services.strategy_runner import strategy_runner

        mock_price.return_value = 24000.0
        mock_candles.return_value = []
        mock_risk.return_value = (True, None)
        mock_ctx.return_value = MagicMock(spec=MarketContext)
        mock_ctx.return_value.india_vix = 16.0

        mock_strategy = MagicMock()
        mock_strategy.evaluate.return_value = None
        mock_get_strategy.return_value = mock_strategy

        result = await strategy_runner.evaluate_manual("NIFTY", StrategyName.VWAP_PULLBACK)
        assert result is None
        mock_handle.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._build_market_context", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._check_global_risk_limits", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._load_todays_candles", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._get_current_price", new_callable=AsyncMock)
    async def test_returns_none_when_no_market_context(
        self, mock_price, mock_candles, mock_risk, mock_ctx
    ):
        """If MarketContext can't be built, return None."""
        from app.services.strategy_runner import strategy_runner

        mock_price.return_value = 24000.0
        mock_candles.return_value = []
        mock_risk.return_value = (True, None)
        mock_ctx.return_value = None

        result = await strategy_runner.evaluate_manual("NIFTY", StrategyName.VWAP_PULLBACK)
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _evaluate_strategies with strategy_filter
# ---------------------------------------------------------------------------


class TestEvaluateStrategiesFilter:
    """Tests that _evaluate_strategies respects the strategy_filter parameter."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.get_active_strategies")
    async def test_uses_filter_when_provided(self, mock_get_active):
        """When strategy_filter is provided, it should use those names, not query DB."""
        from app.services.strategy_runner import strategy_runner

        mock_strategy = MagicMock()
        mock_strategy.evaluate.return_value = None
        mock_strategy.name = "vwap_pullback"
        mock_get_active.return_value = [mock_strategy]

        ctx = MagicMock(spec=MarketContext)

        await strategy_runner._evaluate_strategies(
            "NIFTY", ctx, True, None,
            strategy_filter=[StrategyName.VWAP_PULLBACK],
        )

        # Should call get_active_strategies with the filter, not query DB
        mock_get_active.assert_called_once_with([StrategyName.VWAP_PULLBACK])

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._get_active_strategy_names", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.get_active_strategies")
    async def test_queries_db_when_no_filter(self, mock_get_active, mock_get_names):
        """When strategy_filter is None, it should query the DB for active strategies."""
        from app.services.strategy_runner import strategy_runner

        mock_get_names.return_value = [StrategyName.VWAP_PULLBACK]
        mock_strategy = MagicMock()
        mock_strategy.evaluate.return_value = None
        mock_strategy.name = "vwap_pullback"
        mock_get_active.return_value = [mock_strategy]

        ctx = MagicMock(spec=MarketContext)

        await strategy_runner._evaluate_strategies(
            "NIFTY", ctx, True, None,
            strategy_filter=None,
        )

        mock_get_names.assert_called_once()
        mock_get_active.assert_called_once_with([StrategyName.VWAP_PULLBACK])

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.get_active_strategies")
    async def test_empty_filter_skips_evaluation(self, mock_get_active):
        """When strategy_filter is an empty list, no strategies should be evaluated."""
        from app.services.strategy_runner import strategy_runner

        ctx = MagicMock(spec=MarketContext)

        await strategy_runner._evaluate_strategies(
            "NIFTY", ctx, True, None,
            strategy_filter=[],
        )

        mock_get_active.assert_not_called()
