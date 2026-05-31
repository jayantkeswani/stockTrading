"""Tests for trade_monitor.py — position monitoring, SL/target/time exit, trailing SL, expiry roll."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import (
    AgentActionType,
    ConfirmationStatus,
    ExitReason,
    TradeStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    *,
    entry_price=Decimal("200.0"),
    stop_loss=Decimal("140.0"),
    target_price=Decimal("300.0"),
    current_price=None,
    position_type="INTRADAY",
    strategy_name="vwap_pullback",
    fyers_option_symbol="NSE:NIFTY26MAY24000CE",
    symbol="NIFTY",
    lots=2,
    quantity=150,
    is_paper=True,
    is_shadow=False,
    expiry_date=None,
):
    pos = MagicMock()
    pos.id = uuid.uuid4()
    pos.trade_id = uuid.uuid4()
    pos.symbol = symbol
    pos.entry_price = entry_price
    pos.stop_loss = stop_loss
    pos.target_price = target_price
    pos.current_price = current_price
    pos.unrealized_pnl = None
    pos.fyers_option_symbol = fyers_option_symbol
    pos.strategy_name = strategy_name
    pos.position_type = position_type
    pos.lots = lots
    pos.quantity = quantity
    pos.is_paper = is_paper
    pos.is_shadow = is_shadow
    pos.expiry_date = expiry_date
    pos.strike_price = Decimal("24000")
    pos.option_type = "CE"
    pos.instrument_type = "OPTION"
    pos.high_since_entry = None
    pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    return pos


def _make_trade(pos, status=TradeStatus.OPEN):
    trade = MagicMock()
    trade.id = pos.trade_id
    trade.entry_price = pos.entry_price
    trade.stop_loss = pos.stop_loss
    trade.quantity = pos.quantity
    trade.status = status.value
    trade.pnl = None
    trade.pnl_percent = None
    trade.exit_price = None
    trade.exit_time = None
    trade.exit_reason = None
    return trade


def _mock_db_for_close(trade):
    """Return a mock db session that returns the given trade on select(Trade)."""
    db = AsyncMock()
    trade_result = MagicMock()
    trade_result.scalar_one_or_none.return_value = trade
    db.execute = AsyncMock(return_value=trade_result)
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# SL hit
# ---------------------------------------------------------------------------

class TestSLHit:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_sl_hit", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_sl_hit_closes_position(self, mock_price, mock_notify, mock_ws):
        """When current price <= stop_loss, position is closed with AGENT_SL."""
        pos = _make_position(entry_price=Decimal("200"), stop_loss=Decimal("140"))
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 130.0}
        mock_ws.broadcast = AsyncMock()

        db = _mock_db_for_close(trade)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.SL_TRIGGERED.value
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == ExitReason.AGENT_SL.value
        db.delete.assert_called_once_with(pos)
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_sl_hit", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_sl_hit_exactly_at_stop_loss(self, mock_price, mock_notify, mock_ws):
        """Price exactly at SL triggers close (uses <=)."""
        pos = _make_position(stop_loss=Decimal("140"))
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 140.0}
        mock_ws.broadcast = AsyncMock()

        db = _mock_db_for_close(trade)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.SL_TRIGGERED.value


# ---------------------------------------------------------------------------
# Target hit
# ---------------------------------------------------------------------------

class TestTargetHit:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_profit_booked", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_target_hit_yolo_auto_books(self, mock_price, mock_notify, mock_ws):
        """YOLO mode auto-books profit when price >= target."""
        pos = _make_position(target_price=Decimal("300"))
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 310.0}
        mock_ws.broadcast = AsyncMock()

        db = _mock_db_for_close(trade)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=True)

        assert action is not None
        assert action["action_type"] == AgentActionType.AUTO_PROFIT_BOOKED.value
        assert trade.status == TradeStatus.CLOSED
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_confirmation_request", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_target_hit_semi_requests_confirmation(self, mock_price, mock_notify, mock_ws):
        """SEMI mode requests confirmation when price >= target (doesn't auto-close)."""
        pos = _make_position(target_price=Decimal("300"))
        mock_price.return_value = {"ltp": 310.0}
        mock_ws.broadcast = AsyncMock()

        # DB mock: no existing pending confirmation, then return for AgentLog add
        db = AsyncMock()
        # First execute: position update broadcast (get_cached_price handles this)
        # Then: check for existing pending confirmation → None
        # Then: flush after adding log
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=existing_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.PROFIT_BOOK_REQUEST.value
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_target_hit_semi_dedup_skips_repeat(self, mock_price, mock_ws):
        """SEMI mode skips if a PENDING confirmation already exists."""
        pos = _make_position(target_price=Decimal("300"))
        mock_price.return_value = {"ltp": 310.0}
        mock_ws.broadcast = AsyncMock()

        existing_log = MagicMock()
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = existing_log
        db = AsyncMock()
        db.execute = AsyncMock(return_value=existing_result)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None


# ---------------------------------------------------------------------------
# Time exit
# ---------------------------------------------------------------------------

class TestTimeExit:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=True)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_time_exit", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_intraday_time_exit(self, mock_price, mock_notify, mock_ws, mock_deadline):
        """INTRADAY positions close after 3:25 PM."""
        pos = _make_position(position_type="INTRADAY")
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 210.0}
        mock_ws.broadcast = AsyncMock()

        db = _mock_db_for_close(trade)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.TIME_EXIT.value
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=True)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_positional_no_time_exit(self, mock_price, mock_ws, mock_deadline):
        """POSITIONAL positions do NOT get time-exited at 3:25 PM."""
        pos = _make_position(position_type="POSITIONAL", expiry_date=date.today() + timedelta(days=30))
        mock_price.return_value = {"ltp": 210.0}
        mock_ws.broadcast = AsyncMock()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None


# ---------------------------------------------------------------------------
# Trailing SL
# ---------------------------------------------------------------------------

class TestTrailingSL:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={"trailing_sl_activation_pct": 10.0})
    async def test_trailing_sl_moves_to_breakeven(self, mock_params, mock_price, mock_ws, mock_deadline):
        """POSITIONAL position: 10%+ gain moves SL to entry (breakeven)."""
        pos = _make_position(
            position_type="POSITIONAL",
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("92.0"),
            target_price=Decimal("120.0"),
            expiry_date=date.today() + timedelta(days=30),
        )
        mock_price.return_value = {"ltp": 111.0}
        mock_ws.broadcast = AsyncMock()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None
        assert pos.stop_loss == pos.entry_price

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={"trailing_sl_activation_pct": 10.0})
    async def test_trailing_sl_not_triggered_below_threshold(self, mock_params, mock_price, mock_ws, mock_deadline):
        """POSITIONAL position: 5% gain does NOT move SL."""
        pos = _make_position(
            position_type="POSITIONAL",
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("92.0"),
            target_price=Decimal("120.0"),
            expiry_date=date.today() + timedelta(days=30),
        )
        original_sl = pos.stop_loss
        mock_price.return_value = {"ltp": 105.0}
        mock_ws.broadcast = AsyncMock()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None
        assert pos.stop_loss == original_sl

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={"trailing_sl_activation_pct": 10.0})
    async def test_trailing_sl_already_at_breakeven_no_op(self, mock_params, mock_price, mock_ws, mock_deadline):
        """If SL is already at entry (breakeven), trailing activation is a no-op."""
        pos = _make_position(
            position_type="POSITIONAL",
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("100.0"),
            target_price=Decimal("120.0"),
            expiry_date=date.today() + timedelta(days=30),
        )
        mock_price.return_value = {"ltp": 115.0}
        mock_ws.broadcast = AsyncMock()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None
        assert pos.stop_loss == Decimal("100.0")


# ---------------------------------------------------------------------------
# Expiry roll
# ---------------------------------------------------------------------------

class TestExpiryRoll:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_expiry_roll", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.notify_sl_hit", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={"trailing_sl_activation_pct": 10.0})
    async def test_expiry_roll_yolo_creates_new_position(
        self, mock_params, mock_price, mock_notify_sl, mock_notify_roll, mock_ws, mock_deadline
    ):
        """YOLO mode rolls POSITIONAL futures near expiry — closes old, opens new."""
        expiry = date.today() + timedelta(days=2)
        pos = _make_position(
            position_type="POSITIONAL",
            entry_price=Decimal("1500.0"),
            stop_loss=Decimal("1380.0"),
            target_price=Decimal("1800.0"),
            expiry_date=expiry,
            fyers_option_symbol="NSE:TCS26MAYFUT",
            symbol="TCS",
            strategy_name="can_slim",
        )
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 1550.0}
        mock_ws.broadcast = AsyncMock()

        # Mock DB: first call returns trade (for _close_position), subsequent calls for new objects
        db = AsyncMock()
        trade_result = MagicMock()
        trade_result.scalar_one_or_none.return_value = trade
        db.execute = AsyncMock(return_value=trade_result)
        db.add = MagicMock()
        db.delete = AsyncMock()
        db.flush = AsyncMock()

        # Mock futures resolution
        resolution = MagicMock()
        resolution.ltp = 1560.0
        resolution.expiry_date = expiry + timedelta(days=28)
        resolution.fyers_symbol = "NSE:TCS26JUNFUT"
        resolution.lot_size = 175

        with patch("app.services.futures_resolver.resolve_futures_contract", new_callable=AsyncMock, return_value=resolution), \
             patch("app.data_feed.fyers_ws_client.fyers_ws_client") as mock_fyers_ws:
            mock_fyers_ws.subscribe_symbols = AsyncMock()

            from app.agent.trade_monitor import _check_position
            action = await _check_position(db, pos, yolo_mode=True)

        assert action is not None
        assert action["action_type"] == AgentActionType.EXPIRY_ROLL.value
        assert action["rolled_to"] == str(resolution.expiry_date)
        mock_notify_roll.assert_called_once()


# ---------------------------------------------------------------------------
# Shadow positions
# ---------------------------------------------------------------------------

class TestShadowPositions:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.notify_sl_hit", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_shadow_position_sl_closes_same_as_real(self, mock_price, mock_notify, mock_ws):
        """Shadow positions are monitored the same way — SL triggers close."""
        pos = _make_position(is_shadow=True, stop_loss=Decimal("140"))
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 130.0}
        mock_ws.broadcast = AsyncMock()

        db = _mock_db_for_close(trade)

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.SL_TRIGGERED.value


# ---------------------------------------------------------------------------
# No price available
# ---------------------------------------------------------------------------

class TestNoPriceAvailable:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock, return_value=None)
    @patch("app.agent.trade_monitor._fetch_option_price_rest", new_callable=AsyncMock, return_value=None)
    async def test_returns_none_when_no_price(self, mock_rest, mock_cache):
        """No action taken when price is unavailable from both cache and REST."""
        pos = _make_position()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_returns_none_when_price_zero(self, mock_price):
        """No action when LTP is zero."""
        pos = _make_position()
        mock_price.return_value = {"ltp": 0}

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None


# ---------------------------------------------------------------------------
# No exit conditions met
# ---------------------------------------------------------------------------

class TestIntradayTrailingSL:
    """Strategy 5 intraday trailing: breakeven at 0.5%, progressive trail at 0.3% below HWM."""

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={
        "trailing_sl_enabled": True,
        "trailing_sl_breakeven_pct": 0.5,
        "trailing_sl_trail_pct": 0.3,
    })
    async def test_intraday_breakeven_at_half_pct(self, mock_params, mock_price, mock_ws, mock_deadline):
        """INTRADAY with trailing_sl_enabled: 0.5% gain moves SL to breakeven."""
        pos = _make_position(
            position_type="INTRADAY",
            strategy_name="intraday_futures",
            entry_price=Decimal("1000.0"),
            stop_loss=Decimal("980.0"),
            target_price=Decimal("1030.0"),
        )
        mock_price.return_value = {"ltp": 1006.0}  # 0.6% gain
        mock_ws.broadcast = AsyncMock()
        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        await _check_position(db, pos, yolo_mode=False)

        assert pos.stop_loss >= pos.entry_price  # moved to breakeven or beyond

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={
        "trailing_sl_enabled": True,
        "trailing_sl_breakeven_pct": 0.5,
        "trailing_sl_trail_pct": 0.3,
    })
    async def test_intraday_no_trail_when_disabled(self, mock_params, mock_price, mock_ws, mock_deadline):
        """INTRADAY without trailing_sl_enabled: no trailing at all."""
        mock_params.return_value = {"trailing_sl_enabled": False}
        pos = _make_position(
            position_type="INTRADAY",
            strategy_name="vwap_pullback",
            entry_price=Decimal("1000.0"),
            stop_loss=Decimal("980.0"),
            target_price=Decimal("1030.0"),
        )
        original_sl = pos.stop_loss
        mock_price.return_value = {"ltp": 1010.0}
        mock_ws.broadcast = AsyncMock()
        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        await _check_position(db, pos, yolo_mode=False)

        assert pos.stop_loss == original_sl

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={
        "trailing_sl_enabled": True,
        "trailing_sl_breakeven_pct": 0.5,
        "trailing_sl_trail_pct": 0.3,
    })
    async def test_progressive_trail_moves_sl_up(self, mock_params, mock_price, mock_ws, mock_deadline):
        """Progressive trail: SL moves to HWM * (1 - 0.3%) when SL already at breakeven."""
        pos = _make_position(
            position_type="INTRADAY",
            strategy_name="intraday_futures",
            entry_price=Decimal("1000.0"),
            stop_loss=Decimal("1000.0"),  # already at breakeven
            target_price=Decimal("1030.0"),
        )
        pos.high_since_entry = Decimal("1015.0")
        mock_price.return_value = {"ltp": 1012.0}  # still above entry
        mock_ws.broadcast = AsyncMock()
        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        await _check_position(db, pos, yolo_mode=False)

        # HWM updated to max(1015, 1012) = 1015 (no change)
        # Trail SL = 1015 * (1 - 0.003) = 1011.955
        expected_trail = Decimal("1015.0") * Decimal("0.997")
        assert pos.stop_loss == expected_trail

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={
        "trailing_sl_enabled": True,
        "trailing_sl_breakeven_pct": 0.5,
        "trailing_sl_trail_pct": 0.3,
    })
    async def test_sl_never_moves_down(self, mock_params, mock_price, mock_ws, mock_deadline):
        """Progressive trail only moves SL up, never back down."""
        pos = _make_position(
            position_type="INTRADAY",
            strategy_name="intraday_futures",
            entry_price=Decimal("1000.0"),
            stop_loss=Decimal("1010.0"),  # already above breakeven from prior trail
            target_price=Decimal("1030.0"),
        )
        pos.high_since_entry = Decimal("1012.0")
        # Price still above SL so SL hit doesn't trigger, but trail SL < current SL
        mock_price.return_value = {"ltp": 1011.0}
        mock_ws.broadcast = AsyncMock()
        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        await _check_position(db, pos, yolo_mode=False)

        # HWM stays 1012 (1011 < 1012). Trail SL = 1012 * 0.997 = 1008.964 < current SL 1010 → no change
        assert pos.stop_loss == Decimal("1010.0")

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.services.strategy_params.get_strategy_params_sync", return_value={
        "trailing_sl_enabled": True,
        "trailing_sl_breakeven_pct": 0.5,
        "trailing_sl_trail_pct": 0.3,
    })
    async def test_hwm_updates_on_new_high(self, mock_params, mock_price, mock_ws, mock_deadline):
        """High water mark updates when current price exceeds previous HWM."""
        pos = _make_position(
            position_type="INTRADAY",
            strategy_name="intraday_futures",
            entry_price=Decimal("1000.0"),
            stop_loss=Decimal("1000.0"),
            target_price=Decimal("1030.0"),
        )
        pos.high_since_entry = Decimal("1010.0")
        mock_price.return_value = {"ltp": 1020.0}  # new high
        mock_ws.broadcast = AsyncMock()
        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        await _check_position(db, pos, yolo_mode=False)

        assert pos.high_since_entry == Decimal("1020.0")


class TestNoExit:

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_no_action_when_price_between_sl_and_target(self, mock_price, mock_ws, mock_deadline):
        """No action when price is safely between SL and target."""
        pos = _make_position(
            entry_price=Decimal("200"),
            stop_loss=Decimal("140"),
            target_price=Decimal("300"),
        )
        mock_price.return_value = {"ltp": 220.0}
        mock_ws.broadcast = AsyncMock()

        db = AsyncMock()

        from app.agent.trade_monitor import _check_position
        action = await _check_position(db, pos, yolo_mode=False)

        assert action is None
