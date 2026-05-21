"""Tests for lot_sizing.py — compute_lots_for_shadow / _yolo / _manual."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(strategy_name="vwap_pullback", entry_price=200.0, stop_loss=140.0, indicators=None):
    """Build a minimal mock Signal ORM object for lot sizing tests."""
    s = MagicMock()
    s.strategy_name = strategy_name
    s.entry_price = Decimal(str(entry_price))
    s.stop_loss = Decimal(str(stop_loss))
    s.indicators = indicators or {}
    return s


def _make_cfg(capital=1_000_000, max_risk_per_trade_pct=2.0):
    cfg = MagicMock()
    cfg.capital = capital
    cfg.max_risk_per_trade_pct = max_risk_per_trade_pct
    return cfg


# ---------------------------------------------------------------------------
# compute_lots_for_shadow
# ---------------------------------------------------------------------------


class TestComputeLotsForShadow:
    def test_shadow_always_one_lot(self):
        """Shadow executor always uses exactly 1 lot regardless of lot_size."""
        from app.services.lot_sizing import compute_lots_for_shadow

        result = compute_lots_for_shadow(lot_size=75)
        assert result == 1

    def test_shadow_ignores_large_lot_size(self):
        """1 lot is returned even with a large lot_size argument."""
        from app.services.lot_sizing import compute_lots_for_shadow

        result = compute_lots_for_shadow(lot_size=500)
        assert result == 1

    def test_shadow_ignores_small_lot_size(self):
        """1 lot is returned even with lot_size=1."""
        from app.services.lot_sizing import compute_lots_for_shadow

        result = compute_lots_for_shadow(lot_size=1)
        assert result == 1

    def test_shadow_returns_int(self):
        """Return value is a plain int (not a tuple)."""
        from app.services.lot_sizing import compute_lots_for_shadow

        result = compute_lots_for_shadow(lot_size=30)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# compute_lots_for_yolo — VWAP / non-intraday_futures path
# ---------------------------------------------------------------------------


class TestComputeLotsForYoloVwap:
    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_vwap_strategy_uses_calculate_lots(
        self, mock_get_strategy, mock_calculate_lots, mock_get_cfg
    ):
        """VWAP Pullback signals call calculate_lots with the right parameters."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg(capital=1_000_000, max_risk_per_trade_pct=2.0)
        mock_strategy = MagicMock()
        mock_strategy.max_lots = 5
        mock_get_strategy.return_value = mock_strategy
        mock_calculate_lots.return_value = 3

        signal = _make_signal(strategy_name="vwap_pullback", entry_price=200.0, stop_loss=140.0)
        lots, meta = await compute_lots_for_yolo(signal, lot_size=75)

        assert lots == 3
        mock_calculate_lots.assert_called_once_with(
            capital=1_000_000,
            risk_per_trade_pct=2.0,
            entry_price=200.0,
            stop_loss=140.0,
            lot_size=75,
            vix_multiplier=1.0,  # no VIX passed → multiplier=1.0
            max_lots=5,
        )

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_meta_contains_expected_keys(
        self, mock_get_strategy, mock_calculate_lots, mock_get_cfg
    ):
        """sizing_meta dict contains capital, risk_pct, vix, lot_size, and strategy."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()
        mock_get_strategy.return_value = MagicMock(max_lots=None)
        mock_calculate_lots.return_value = 2

        signal = _make_signal(strategy_name="vwap_pullback")
        _, meta = await compute_lots_for_yolo(signal, lot_size=30, india_vix=16.0)

        assert meta["strategy"] == "vwap_pullback"
        assert meta["lot_size"] == 30
        assert meta["vix"] == 16.0
        assert "capital" in meta
        assert "risk_pct" in meta
        assert "vix_multiplier" in meta
        assert "max_lots" in meta


# ---------------------------------------------------------------------------
# compute_lots_for_yolo — intraday_futures path
# ---------------------------------------------------------------------------


class TestComputeLotsForYoloS5:
    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_s5_strategy_uses_compute_lots(self, mock_get_strategy, mock_get_cfg):
        """intraday_futures signals call strategy._compute_lots, not calculate_lots."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()

        mock_strategy = MagicMock()
        mock_strategy.max_lots = 2
        mock_strategy._compute_lots.return_value = 1
        mock_get_strategy.return_value = mock_strategy

        indicators = {
            "rvol": 1.8,
            "briefing_max_lots": 2,
            "nifty_bias_strength": "MODERATE",
            "screener_score": 72,
            "briefing_approach": "SELECTIVE",
            "stock_trend_direction": "BULLISH",
            "stock_trend_strength": "MODERATE",
            "enhanced_orb": True,
            "setup_type": "ORB",
        }
        signal = _make_signal(strategy_name="intraday_futures", indicators=indicators)

        lots, meta = await compute_lots_for_yolo(signal, lot_size=75, india_vix=15.0)

        assert lots == 1
        mock_strategy._compute_lots.assert_called_once()

        call_kwargs = mock_strategy._compute_lots.call_args
        passed_params = call_kwargs.kwargs.get("params") or call_kwargs.args[1]

        assert passed_params["_india_vix"] == 15.0
        assert passed_params["_briefing_max_lots"] == 2
        assert passed_params["_nifty_bias_strength"] == "MODERATE"
        assert passed_params["_screener_score"] == 72
        assert passed_params["_briefing_approach"] == "SELECTIVE"
        assert passed_params["_stock_trend_direction"] == "BULLISH"
        assert passed_params["_stock_trend_strength"] == "MODERATE"
        assert passed_params["_enhanced_orb"] is True
        assert passed_params["setup_type"] == "ORB"

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_s5_passes_indicators_kwarg(self, mock_get_strategy, mock_get_cfg):
        """_compute_lots is called with the full indicators dict as indicators= kwarg."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()

        mock_strategy = MagicMock()
        mock_strategy.max_lots = 2
        mock_strategy._compute_lots.return_value = 2
        mock_get_strategy.return_value = mock_strategy

        indicators = {"rvol": 2.0, "setup_type": "VWAP_BOUNCE"}
        signal = _make_signal(strategy_name="intraday_futures", indicators=indicators)

        await compute_lots_for_yolo(signal, lot_size=75)

        call_kwargs = mock_strategy._compute_lots.call_args
        passed_indicators = call_kwargs.kwargs.get("indicators") or call_kwargs.args[2]
        assert passed_indicators == indicators

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_s5_falls_back_when_no_compute_lots(
        self, mock_get_strategy, mock_calculate_lots, mock_get_cfg
    ):
        """If the resolved strategy lacks _compute_lots, falls back to calculate_lots."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()
        mock_strategy = MagicMock(spec=[])  # no _compute_lots attribute
        mock_strategy.max_lots = None
        mock_get_strategy.return_value = mock_strategy
        mock_calculate_lots.return_value = 1

        signal = _make_signal(strategy_name="intraday_futures")

        lots, _ = await compute_lots_for_yolo(signal, lot_size=75)

        assert lots == 1
        mock_calculate_lots.assert_called_once()


# ---------------------------------------------------------------------------
# compute_lots_for_manual — delegates to YOLO
# ---------------------------------------------------------------------------


class TestComputeLotsForManual:
    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.compute_lots_for_yolo")
    async def test_manual_delegates_to_yolo(self, mock_yolo):
        """compute_lots_for_manual must return exactly what compute_lots_for_yolo returns."""
        from app.services.lot_sizing import compute_lots_for_manual

        mock_yolo.return_value = (4, {"capital": 1_000_000, "strategy": "vwap_pullback"})
        signal = _make_signal()

        result = await compute_lots_for_manual(signal, lot_size=75, india_vix=14.0)

        assert result == (4, {"capital": 1_000_000, "strategy": "vwap_pullback"})
        mock_yolo.assert_awaited_once_with(signal, 75, 14.0)

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.compute_lots_for_yolo")
    async def test_manual_passes_none_vix_when_omitted(self, mock_yolo):
        """india_vix defaults to None and is forwarded unchanged to YOLO."""
        from app.services.lot_sizing import compute_lots_for_manual

        mock_yolo.return_value = (2, {})
        signal = _make_signal()

        await compute_lots_for_manual(signal, lot_size=30)

        mock_yolo.assert_awaited_once_with(signal, 30, None)


# ---------------------------------------------------------------------------
# VIX multiplier pass-through
# ---------------------------------------------------------------------------


class TestYoloWithVix:
    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.services.lot_sizing.vix_to_multiplier")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_with_vix_passes_multiplier_to_calculate_lots(
        self, mock_get_strategy, mock_vix_mult, mock_calculate_lots, mock_get_cfg
    ):
        """VIX level is converted to a multiplier and forwarded to calculate_lots."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()
        mock_get_strategy.return_value = MagicMock(max_lots=None)
        mock_vix_mult.return_value = 0.9  # simulate VIX 18-22 → 0.9
        mock_calculate_lots.return_value = 2

        signal = _make_signal(strategy_name="vwap_pullback")
        lots, meta = await compute_lots_for_yolo(signal, lot_size=75, india_vix=20.0)

        mock_vix_mult.assert_called_once_with(20.0)
        mock_calculate_lots.assert_called_once()
        call_kwargs = mock_calculate_lots.call_args
        assert call_kwargs.kwargs["vix_multiplier"] == 0.9
        assert meta["vix"] == 20.0
        assert meta["vix_multiplier"] == 0.9

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.services.lot_sizing.vix_to_multiplier")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_with_no_vix_uses_multiplier_one(
        self, mock_get_strategy, mock_vix_mult, mock_calculate_lots, mock_get_cfg
    ):
        """When india_vix is None, vix_to_multiplier returns 1.0 (no adjustment)."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()
        mock_get_strategy.return_value = MagicMock(max_lots=None)
        mock_vix_mult.return_value = 1.0
        mock_calculate_lots.return_value = 3

        signal = _make_signal()
        _, meta = await compute_lots_for_yolo(signal, lot_size=75)

        mock_vix_mult.assert_called_once_with(None)
        assert meta["vix"] is None
        assert meta["vix_multiplier"] == 1.0

    @pytest.mark.asyncio
    @patch("app.services.lot_sizing.get_trading_config")
    @patch("app.services.lot_sizing.calculate_lots")
    @patch("app.services.lot_sizing.vix_to_multiplier")
    @patch("app.strategies.registry.get_strategy")
    async def test_yolo_low_vix_uses_higher_multiplier(
        self, mock_get_strategy, mock_vix_mult, mock_calculate_lots, mock_get_cfg
    ):
        """Low VIX (< 14) → multiplier 1.1 so more lots are allocated."""
        from app.services.lot_sizing import compute_lots_for_yolo

        mock_get_cfg.return_value = _make_cfg()
        mock_get_strategy.return_value = MagicMock(max_lots=None)
        mock_vix_mult.return_value = 1.1
        mock_calculate_lots.return_value = 4

        signal = _make_signal()
        _, meta = await compute_lots_for_yolo(signal, lot_size=75, india_vix=12.0)

        mock_vix_mult.assert_called_once_with(12.0)
        assert meta["vix_multiplier"] == 1.1
