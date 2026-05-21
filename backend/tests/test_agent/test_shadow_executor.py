"""Tests for shadow_executor.py — signal accuracy shadow agent."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import AgentActionType, SignalStatus, TradeSource


class TestShadowExecuteSignal:

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price")
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_creates_trade_and_position(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """A PENDING signal — even executable=False — produces Trade(SHADOW) + Position(is_shadow=True)."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, executable=False, blocked_reason="Outside trade window")

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()
        mock_price.return_value = 180.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        positions = [o for o in added_objects if type(o).__name__ == "Position"]
        logs = [o for o in added_objects if type(o).__name__ == "AgentLog"]

        assert len(trades) == 1
        assert trades[0].source == TradeSource.SHADOW.value
        assert trades[0].is_paper is True
        assert trades[0].entry_price == 180.0

        assert len(positions) == 1
        assert positions[0].is_shadow is True
        assert positions[0].is_paper is True

        assert len(logs) == 1
        assert logs[0].action_type == AgentActionType.SHADOW_EXECUTED.value
        assert logs[0].details["executable"] is False
        assert logs[0].details["blocked_reason"] == "Outside trade window"

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price")
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_fires_for_executable_signal_too(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Shadow also runs when executable=True (normal case)."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, executable=True)

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()
        mock_price.return_value = 200.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        assert len(trades) == 1
        assert trades[0].source == TradeSource.SHADOW.value

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.async_session_factory")
    async def test_shadow_skips_non_pending_signal(self, mock_session_factory):
        """Does not create trade if signal is already EXECUTED."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, status=SignalStatus.EXECUTED)

        session, added_objects = _mock_session(mock_session_factory, signal)

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        assert added_objects == []

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.async_session_factory")
    async def test_shadow_skips_missing_signal(self, mock_session_factory):
        """Does not raise if signal not found — returns silently."""
        session, added_objects = _mock_session(mock_session_factory, None)

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(uuid.uuid4())  # should not raise

        assert added_objects == []

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price")
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_no_dedup_two_calls_same_symbol(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Two signals on the same symbol both produce shadow trades — no dedup."""
        signal_id_1 = uuid.uuid4()
        signal_id_2 = uuid.uuid4()
        added_1: list = []
        added_2: list = []

        mock_cfg.return_value = _make_cfg()
        mock_price.return_value = 150.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal

        # Run both independently
        mock_session_factory.return_value.__aenter__ = AsyncMock(
            side_effect=[
                _make_raw_session(_make_signal(signal_id_1), added_1),
                _make_raw_session(_make_signal(signal_id_2), added_2),
            ]
        )
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await shadow_execute_signal(signal_id_1)
        await shadow_execute_signal(signal_id_2)

        trades_1 = [o for o in added_1 if type(o).__name__ == "Trade"]
        trades_2 = [o for o in added_2 if type(o).__name__ == "Trade"]
        assert len(trades_1) == 1
        assert len(trades_2) == 1

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price", side_effect=Exception("timeout"))
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_falls_back_to_signal_price_on_live_price_failure(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Falls back to signal.entry_price if get_live_price raises."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, entry_price=Decimal("175.0"))

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        assert len(trades) == 1
        assert float(trades[0].entry_price) == 175.0

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_skips_low_confidence_signal(self, mock_deadline, mock_session_factory, mock_cfg):
        """Signal below min_confidence_for_shadow threshold produces no trade."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, confidence=Decimal("30.0"))

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg(min_confidence_for_shadow=45.0)

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        assert added_objects == []

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price")
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_fires_at_exact_threshold(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Signal at exactly min_confidence_for_shadow should still fire."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id, confidence=Decimal("45.0"))

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg(min_confidence_for_shadow=45.0)
        mock_price.return_value = 180.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        assert len(trades) == 1

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_skips_permanent_watchlist_signal(self, mock_deadline, mock_session_factory, mock_cfg):
        """Signal with is_permanent_watchlist=True + config skip=True → no trade."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id)
        signal.is_permanent_watchlist = True

        session, added_objects = _mock_session(mock_session_factory, signal)
        mock_cfg.return_value = _make_cfg()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        assert added_objects == []

    @pytest.mark.asyncio
    @patch("app.agent.shadow_executor.ws_manager")
    @patch("app.agent.shadow_executor.get_live_price")
    @patch("app.agent.shadow_executor.get_trading_config")
    @patch("app.agent.shadow_executor.async_session_factory")
    @patch("app.agent.shadow_executor.is_past_close_deadline", return_value=False)
    async def test_shadow_fires_permanent_watchlist_when_config_off(
        self, mock_deadline, mock_session_factory, mock_cfg, mock_price, mock_ws
    ):
        """Signal with is_permanent_watchlist=True but config skip=False → trade created."""
        signal_id = uuid.uuid4()
        signal = _make_signal(signal_id)
        signal.is_permanent_watchlist = True

        session, added_objects = _mock_session(mock_session_factory, signal)
        cfg = _make_cfg()
        cfg.shadow_skip_permanent_watchlist = False
        mock_cfg.return_value = cfg
        mock_price.return_value = 180.0
        mock_ws.broadcast = AsyncMock()

        from app.agent.shadow_executor import shadow_execute_signal
        await shadow_execute_signal(signal_id)

        trades = [o for o in added_objects if type(o).__name__ == "Trade"]
        assert len(trades) == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(signal_id, executable=True, blocked_reason=None, status=None, entry_price=None, confidence=None):
    s = MagicMock()
    s.id = signal_id
    s.status = (status or SignalStatus.PENDING).value if status else SignalStatus.PENDING.value
    s.executable = executable
    s.blocked_reason = blocked_reason
    s.instrument_type = "OPTION"
    s.signal_type = "BUY_CE"
    s.symbol = "NIFTY"
    s.strategy_name = "vwap_pullback"
    s.expiry_date = "2026-05-06"
    s.strike_price = Decimal("24000")
    s.stop_loss = Decimal("120.0")
    s.target_price = Decimal("270.0")
    s.entry_price = entry_price or Decimal("180.0")
    s.confidence = confidence if confidence is not None else Decimal("70.0")
    s.fyers_option_symbol = "NSE:NIFTY26MAY24000CE"
    s.fyers_futures_symbol = None
    s.lots = 2
    s.indicators = {}
    s.is_permanent_watchlist = False
    return s


def _make_cfg(min_confidence_for_shadow=45.0, min_confidence_for_execution=60.0):
    cfg = MagicMock()
    cfg.capital = 1_000_000
    cfg.max_risk_per_trade_pct = 1.5
    cfg.paper_trading = True
    cfg.min_confidence_for_shadow = min_confidence_for_shadow
    cfg.min_confidence_for_execution = min_confidence_for_execution
    cfg.shadow_skip_permanent_watchlist = True
    cfg.yolo_skip_permanent_watchlist = True
    return cfg


def _mock_session(mock_session_factory, signal, added_objects=None):
    if added_objects is None:
        added_objects = []
    session = _make_raw_session(signal, added_objects)
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return session, added_objects


def _make_raw_session(signal, added_objects):
    session = AsyncMock()
    signal_result = MagicMock()
    signal_result.scalar_one_or_none.return_value = signal
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    # First execute = signal lookup, second = shadow trade dedup check
    session.execute = AsyncMock(side_effect=[signal_result, no_result])
    session.add = lambda obj: added_objects.append(obj)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session
