"""Tests for EOD signal expiry task."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.enums import SignalStatus, StrategyName


class TestExpireIntradaySignals:
    """Test expire_intraday_signals() bulk-rejects PENDING intraday signals."""

    @pytest.mark.asyncio
    @patch("app.tasks.signal_expiry_task.is_trading_day", return_value=False)
    @patch("app.tasks.signal_expiry_task.now_ist")
    async def test_skips_non_trading_day(self, mock_now, mock_td):
        from app.tasks.signal_expiry_task import expire_intraday_signals

        mock_now.return_value = datetime(2026, 5, 24, 15, 30)  # Saturday
        await expire_intraday_signals()
        # Should return early without touching DB

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.tasks.signal_expiry_task.is_trading_day", return_value=True)
    @patch("app.tasks.signal_expiry_task.now_ist")
    async def test_expires_pending_intraday_signals(self, mock_now, mock_td, mock_sf):
        from app.tasks.signal_expiry_task import expire_intraday_signals

        mock_now.return_value = datetime(2026, 5, 22, 15, 30)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        await expire_intraday_signals()

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.tasks.signal_expiry_task.is_trading_day", return_value=True)
    @patch("app.tasks.signal_expiry_task.now_ist")
    async def test_no_signals_to_expire(self, mock_now, mock_td, mock_sf):
        from app.tasks.signal_expiry_task import expire_intraday_signals

        mock_now.return_value = datetime(2026, 5, 22, 15, 30)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        await expire_intraday_signals()

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.tasks.signal_expiry_task.is_trading_day", return_value=True)
    @patch("app.tasks.signal_expiry_task.now_ist")
    async def test_handles_db_exception_gracefully(self, mock_now, mock_td, mock_sf):
        from app.tasks.signal_expiry_task import expire_intraday_signals

        mock_now.return_value = datetime(2026, 5, 22, 15, 30)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await expire_intraday_signals()

    def test_intraday_strategies_tuple_contents(self):
        """Verify the intraday strategies list matches expected values."""
        from app.tasks.signal_expiry_task import _INTRADAY_STRATEGIES

        assert StrategyName.VWAP_PULLBACK.value in _INTRADAY_STRATEGIES
        assert StrategyName.INTRADAY_FUTURES.value in _INTRADAY_STRATEGIES
        assert StrategyName.ORB.value in _INTRADAY_STRATEGIES
        assert StrategyName.GAMMA_SCALPING.value in _INTRADAY_STRATEGIES
        assert StrategyName.CAN_SLIM.value not in _INTRADAY_STRATEGIES
