"""Tests for auto_executor.py — YOLO permanent watchlist skip gate."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import SignalStatus, TradeSource


class TestYoloPermanentWatchlistSkip:

    @pytest.mark.asyncio
    @patch("app.agent.auto_executor.ws_manager")
    @patch("app.agent.auto_executor.get_live_price")
    @patch("app.agent.auto_executor.get_trading_config")
    @patch("app.agent.auto_executor.async_session_factory")
    async def test_yolo_skips_permanent_watchlist_signal(
        self, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Signal with is_permanent_watchlist=True + config skip=True → returns None."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id)
        signal.is_permanent_watchlist = True

        _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()

        from app.agent.auto_executor import auto_execute_signal
        result = await auto_execute_signal(signal_id)

        assert result is None

    @pytest.mark.asyncio
    @patch("app.agent.auto_executor.notify_auto_executed", new_callable=AsyncMock)
    @patch("app.agent.auto_executor._final_risk_check", return_value=(True, None))
    @patch("app.agent.auto_executor.ws_manager")
    @patch("app.agent.auto_executor.get_live_price")
    @patch("app.agent.auto_executor.get_trading_config")
    @patch("app.agent.auto_executor.async_session_factory")
    async def test_yolo_fires_permanent_watchlist_when_config_off(
        self, mock_session_factory, mock_cfg, mock_price, mock_ws, mock_risk, mock_notify
    ):
        """Signal with is_permanent_watchlist=True but config skip=False → trade created."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id)
        signal.is_permanent_watchlist = True

        session, added_objects = _mock_session(mock_session_factory, signal)
        cfg = _make_cfg()
        cfg.yolo_skip_permanent_watchlist = False
        mock_cfg.return_value = cfg
        mock_price.return_value = 180.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.auto_executor import auto_execute_signal
        result = await auto_execute_signal(signal_id)

        assert result is not None
        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        assert len(trades) == 1
        assert trades[0].source == TradeSource.YOLO.value
        assert trades[0].margin_required is not None
        positions = [o for o in added_objects if type(o).__name__ == "Position"]
        assert len(positions) == 1
        assert positions[0].margin_required is not None


class TestYoloFullExecution:

    @pytest.mark.asyncio
    @patch("app.agent.auto_executor.notify_auto_executed", new_callable=AsyncMock)
    @patch("app.agent.auto_executor._final_risk_check", return_value=(True, None))
    @patch("app.agent.auto_executor.ws_manager")
    @patch("app.agent.auto_executor.get_live_price")
    @patch("app.agent.auto_executor.get_trading_config")
    @patch("app.agent.auto_executor.async_session_factory")
    async def test_yolo_creates_trade_with_margin_and_lots(
        self, mock_session_factory, mock_cfg, mock_price, mock_ws, mock_risk, mock_notify
    ):
        """Full YOLO execution path: trade + position created with margin_required set."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id)

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()
        mock_price.return_value = 420.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.auto_executor import auto_execute_signal
        result = await auto_execute_signal(signal_id)

        assert result is not None
        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        positions = [o for o in added_objects if type(o).__name__ == "Position"]

        assert len(trades) == 1
        assert trades[0].source == TradeSource.YOLO.value
        assert trades[0].entry_price == 420.0
        assert trades[0].margin_required is not None
        assert trades[0].margin_required > 0

        assert len(positions) == 1
        assert positions[0].margin_required is not None
        assert positions[0].margin_required > 0
        # Both should have the same margin value
        assert trades[0].margin_required == positions[0].margin_required


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(signal_id, executable=True, confidence=None):
    s = MagicMock()
    s.id = signal_id
    s.status = SignalStatus.PENDING.value
    s.executable = executable
    s.blocked_reason = None
    s.instrument_type = "FUTURE"
    s.signal_type = "BUY_FUT"
    s.symbol = "VEDL"
    s.strategy_name = "intraday_futures"
    s.expiry_date = "2026-05-29"
    s.strike_price = Decimal("0")
    s.stop_loss = Decimal("400.0")
    s.target_price = Decimal("450.0")
    s.entry_price = Decimal("420.0")
    s.confidence = confidence if confidence is not None else Decimal("80.0")
    s.fyers_option_symbol = None
    s.fyers_futures_symbol = "NSE:VEDL26MAYFUT"
    s.indicators = {"futures_lot_size": 50}
    s.is_permanent_watchlist = False
    return s


def _make_cfg():
    cfg = MagicMock()
    cfg.capital = 1_000_000
    cfg.max_risk_per_trade_pct = 1.5
    cfg.paper_trading = True
    cfg.min_confidence_for_execution = 70.0
    cfg.yolo_skip_permanent_watchlist = True
    cfg.shadow_skip_permanent_watchlist = True
    return cfg


def _mock_session(mock_session_factory, signal, added_objects=None):
    if added_objects is None:
        added_objects = []
    session = AsyncMock()
    signal_result = MagicMock()
    signal_result.scalar_one_or_none.return_value = signal
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    # Order: (1) signal lookup, (2) StrategyConfig lookup, (3) signal_id trade dedup, (4) position dedup
    strategy_cfg_result = MagicMock()
    strategy_cfg_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(side_effect=[signal_result, strategy_cfg_result, no_result, no_result])
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return session, added_objects
