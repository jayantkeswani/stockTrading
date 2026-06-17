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


# ---------------------------------------------------------------------------
# Thesis-invalidation exit (S5 YOLO per-profile policy)
# ---------------------------------------------------------------------------

from app.core.enums import DayBias, StrategyName  # noqa: E402
from app.indicators.intraday_bias import IntradayBias  # noqa: E402
from app.services.yolo_profile_service import YoloProfileDTO  # noqa: E402


def _bias(direction: DayBias, strength: str) -> IntradayBias:
    score = {"STRONG": 0.7, "MODERATE": 0.3, "WEAK": 0.1}[strength]
    if direction == DayBias.BEARISH:
        score = -score
    elif direction == DayBias.NEUTRAL:
        score = 0.0
    return IntradayBias(bias=direction, score=score, strength=strength, components={})


def _inval_profile(profile_id, *, persist=3, quorum=False, strong_only=True, name="INVAL") -> YoloProfileDTO:
    return YoloProfileDTO(
        id=profile_id,
        name=name,
        profit_cap=10000.0,
        is_active=True,
        sort_order=0,
        invalidation_persist=persist,
        invalidation_quorum=quorum,
        invalidation_strong_only=strong_only,
    )


def _s5_long(profile_id):
    """Eligible S5 YOLO LONG futures position (target above entry)."""
    pos = _make_position(
        entry_price=Decimal("1000"),
        stop_loss=Decimal("960"),
        target_price=Decimal("1080"),
        strategy_name=StrategyName.INTRADAY_FUTURES.value,
        fyers_option_symbol="NSE:VEDL26JUNFUT",
        symbol="VEDL",
    )
    pos.instrument_type = "FUTURE"
    pos.option_type = ""
    pos.yolo_profile_id = profile_id
    return pos


class TestInvalidationExit:
    """Per-profile S5 thesis-invalidation exit + candle-aligned counter."""

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        import app.agent.trade_monitor as tm
        tm._invalidation_state.clear()
        yield
        tm._invalidation_state.clear()

    @pytest.mark.asyncio
    async def test_counter_closes_long_after_persist_candles(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=3)

        bearish = _bias(DayBias.BEARISH, "STRONG")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [
            (bearish, "c0"), (bearish, "c1"), (bearish, "c2"), (bearish, "c3"),
        ]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner), \
             patch("app.agent.trade_monitor.ws_manager") as mock_ws, \
             patch("app.agent.trade_monitor.notify_invalidation_exit", new_callable=AsyncMock) as mock_notify, \
             patch("app.services.yolo_profile_service.get_default_profile_sync", return_value=profile):
            mock_ws.broadcast = AsyncMock()
            from app.agent.trade_monitor import _check_invalidation
            db = _mock_db_for_close(_make_trade(pos))

            # c0 baseline, c1=count1, c2=count2 → no exit yet
            for _ in range(3):
                assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None
            # c3 → count 3 == persist → close
            action = await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False)

        assert action is not None
        assert action["action_type"] == AgentActionType.INVALIDATION_CLOSE.value
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_opposing_candle_resets_counter(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=2)

        bearish = _bias(DayBias.BEARISH, "STRONG")
        neutral = _bias(DayBias.NEUTRAL, "WEAK")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [
            (bearish, "c0"), (bearish, "c1"), (neutral, "c2"), (bearish, "c3"), (bearish, "c4"),
        ]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner), \
             patch("app.agent.trade_monitor.ws_manager") as mock_ws, \
             patch("app.agent.trade_monitor.notify_invalidation_exit", new_callable=AsyncMock), \
             patch("app.services.yolo_profile_service.get_default_profile_sync", return_value=profile):
            mock_ws.broadcast = AsyncMock()
            from app.agent.trade_monitor import _check_invalidation
            db = _mock_db_for_close(_make_trade(pos))

            results = [await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) for _ in range(5)]

        # baseline, count1, reset0, count1, count2->close
        assert results[:4] == [None, None, None, None]
        assert results[4] is not None

    @pytest.mark.asyncio
    async def test_same_candle_does_not_double_count(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=2)

        bearish = _bias(DayBias.BEARISH, "STRONG")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [
            (bearish, "c0"),  # baseline
            (bearish, "c1"), (bearish, "c1"), (bearish, "c1"),  # one candle, polled 3x
        ]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner), \
             patch("app.agent.trade_monitor.ws_manager") as mock_ws, \
             patch("app.agent.trade_monitor.notify_invalidation_exit", new_callable=AsyncMock):
            mock_ws.broadcast = AsyncMock()
            from app.agent.trade_monitor import _check_invalidation
            db = AsyncMock()
            for _ in range(4):
                assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None

        import app.agent.trade_monitor as tm
        assert tm._invalidation_state[pos.id] == ("c1", 1)

    @pytest.mark.asyncio
    async def test_short_opposed_by_bullish(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        pos.target_price = Decimal("920")   # SHORT: target below entry
        pos.stop_loss = Decimal("1040")
        profile = _inval_profile(pid, persist=1)

        bullish = _bias(DayBias.BULLISH, "STRONG")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [(bullish, "c0"), (bullish, "c1")]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner), \
             patch("app.agent.trade_monitor.ws_manager") as mock_ws, \
             patch("app.agent.trade_monitor.notify_invalidation_exit", new_callable=AsyncMock), \
             patch("app.services.yolo_profile_service.get_default_profile_sync", return_value=profile):
            mock_ws.broadcast = AsyncMock()
            from app.agent.trade_monitor import _check_invalidation
            db = _mock_db_for_close(_make_trade(pos))
            assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=True) is None  # baseline
            action = await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=True)

        assert action is not None
        assert action["details"]["reason"] == ExitReason.INVALIDATION.value

    @pytest.mark.asyncio
    async def test_strong_only_ignores_moderate_bias(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=1, strong_only=True)

        moderate = _bias(DayBias.BEARISH, "MODERATE")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [(moderate, "c0"), (moderate, "c1"), (moderate, "c2")]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner):
            from app.agent.trade_monitor import _check_invalidation
            db = AsyncMock()
            for _ in range(3):
                assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None

    @pytest.mark.asyncio
    async def test_moderate_counts_when_strong_only_false(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=1, strong_only=False)

        moderate = _bias(DayBias.BEARISH, "MODERATE")
        runner = MagicMock()
        runner.nifty_bias_snapshot.side_effect = [(moderate, "c0"), (moderate, "c1")]

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner), \
             patch("app.agent.trade_monitor.ws_manager") as mock_ws, \
             patch("app.agent.trade_monitor.notify_invalidation_exit", new_callable=AsyncMock), \
             patch("app.services.yolo_profile_service.get_default_profile_sync", return_value=profile):
            mock_ws.broadcast = AsyncMock()
            from app.agent.trade_monitor import _check_invalidation
            db = _mock_db_for_close(_make_trade(pos))
            assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None  # baseline
            action = await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False)

        assert action is not None

    @pytest.mark.asyncio
    async def test_disabled_profile_no_exit(self):
        pid = uuid.uuid4()
        pos = _s5_long(pid)
        profile = _inval_profile(pid, persist=None)  # disabled

        bearish = _bias(DayBias.BEARISH, "STRONG")
        runner = MagicMock()
        runner.nifty_bias_snapshot.return_value = (bearish, "c1")

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner):
            from app.agent.trade_monitor import _check_invalidation
            db = AsyncMock()
            for _ in range(5):
                assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None

    @pytest.mark.asyncio
    async def test_shadow_and_non_s5_positions_skipped(self):
        pid = uuid.uuid4()
        profile = _inval_profile(pid, persist=1)
        bearish = _bias(DayBias.BEARISH, "STRONG")
        runner = MagicMock()
        runner.nifty_bias_snapshot.return_value = (bearish, "c1")

        shadow = _s5_long(pid)
        shadow.is_shadow = True
        non_s5 = _s5_long(pid)
        non_s5.strategy_name = StrategyName.VWAP_PULLBACK.value
        no_profile = _s5_long(None)

        with patch("app.services.yolo_profile_service.get_active_profiles_sync", return_value=[profile]), \
             patch("app.services.strategy_runner.strategy_runner", runner):
            from app.agent.trade_monitor import _check_invalidation
            db = AsyncMock()
            for pos in (shadow, non_s5, no_profile):
                assert await _check_invalidation(db, pos, Decimal("1000"), is_short_pos=False) is None


# ---------------------------------------------------------------------------
# Profit-cap valuation — _unrealized_net_pnl books at the exit side of the book
# ---------------------------------------------------------------------------

def _make_trade_for_pos(pos, *, side="BUY", option_type="CE"):
    trade = MagicMock()
    trade.id = pos.trade_id
    trade.side = side
    trade.option_type = option_type
    return trade


def _pnl_db(rows):
    """DB session mock whose execute().all() returns (Position, Trade) rows."""
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


def _expected_net(entry, exit_px, qty, side, instrument_type):
    from app.services.brokerage_calculator import compute_charges
    gross = float((entry - exit_px) * qty) if side == "SELL" else float((exit_px - entry) * qty)
    charges = compute_charges(instrument_type, entry, exit_px, qty, side)
    return gross - float(charges.total)


def _quote_patches(quote, fill_model="BID_ASK"):
    cfg = MagicMock(fill_model=fill_model)
    return (
        patch(
            "app.agent.trade_monitor.get_cached_price",
            new_callable=AsyncMock, return_value=quote,
        ),
        patch(
            "app.services.trading_config.get_trading_config_sync",
            return_value=cfg,
        ),
    )


class TestUnrealizedNetPnlBookValuation:
    """The cap trigger values open positions at the bookable exit price."""

    @pytest.mark.asyncio
    async def test_long_valued_at_bid_not_ltp(self):
        pos = _make_position(entry_price=Decimal("200"), quantity=150)
        rows = [(pos, _make_trade_for_pos(pos))]
        p1, p2 = _quote_patches({"ltp": 220.0, "bid": 218.0, "ask": 221.0})
        with p1, p2:
            from app.agent.trade_monitor import _unrealized_net_pnl
            total = await _unrealized_net_pnl(_pnl_db(rows))

        expected = _expected_net(Decimal("200"), Decimal("218.0"), 150, "BUY", "OPTION")
        assert total == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_short_futures_valued_at_ask(self):
        pos = _make_position(
            entry_price=Decimal("1000"), quantity=500,
            fyers_option_symbol="NSE:VEDL26JUNFUT", symbol="VEDL",
        )
        rows = [(pos, _make_trade_for_pos(pos, side="SELL", option_type=None))]
        p1, p2 = _quote_patches({"ltp": 990.0, "bid": 989.0, "ask": 991.0})
        with p1, p2:
            from app.agent.trade_monitor import _unrealized_net_pnl
            total = await _unrealized_net_pnl(_pnl_db(rows))

        # short exit BUYs at the ask (991), not LTP (990)
        expected = _expected_net(Decimal("1000"), Decimal("991.0"), 500, "SELL", "FUTURE")
        assert total == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_missing_book_falls_back_to_ltp(self):
        pos = _make_position(entry_price=Decimal("200"), quantity=150)
        rows = [(pos, _make_trade_for_pos(pos))]
        p1, p2 = _quote_patches({"ltp": 220.0, "bid": 0, "ask": 0})
        with p1, p2:
            from app.agent.trade_monitor import _unrealized_net_pnl
            total = await _unrealized_net_pnl(_pnl_db(rows))

        expected = _expected_net(Decimal("200"), Decimal("220.0"), 150, "BUY", "OPTION")
        assert total == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_ltp_regime_ignores_book(self):
        pos = _make_position(entry_price=Decimal("200"), quantity=150)
        rows = [(pos, _make_trade_for_pos(pos))]
        p1, p2 = _quote_patches(
            {"ltp": 220.0, "bid": 218.0, "ask": 221.0}, fill_model="LTP",
        )
        with p1, p2:
            from app.agent.trade_monitor import _unrealized_net_pnl
            total = await _unrealized_net_pnl(_pnl_db(rows))

        expected = _expected_net(Decimal("200"), Decimal("220.0"), 150, "BUY", "OPTION")
        assert total == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Per-lot MTM loss stop + daily loss cap (per-profile)
# ---------------------------------------------------------------------------

def _capped_profile(profile_id, *, profit_cap=10000.0, loss_cap=None,
                    per_lot_loss_stop=None, name="P") -> YoloProfileDTO:
    return YoloProfileDTO(
        id=profile_id, name=name, profit_cap=profit_cap, is_active=True, sort_order=0,
        loss_cap=loss_cap, per_lot_loss_stop=per_lot_loss_stop,
    )


class TestPerLotLossStop:
    """Per-position per-lot MTM loss stop — closes a single position before the structural SL."""

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_per_lot_stop_fires_before_structural_sl(self, mock_price, mock_ws):
        # Long futures: entry 1000, SL 960 (loss 40*200=8000 → 4000/lot at SL), per-lot stop
        # 3000 → fires at a 30-pt drop (price 970, still above the 960 SL).
        pid = uuid.uuid4()
        pos = _make_position(
            entry_price=Decimal("1000"), stop_loss=Decimal("960"),
            target_price=Decimal("1080"), lots=2, quantity=200,
            strategy_name=StrategyName.INTRADAY_FUTURES.value,
            fyers_option_symbol="NSE:VEDL26JUNFUT", symbol="VEDL",
        )
        pos.instrument_type = "FUTURE"
        pos.option_type = ""
        pos.yolo_profile_id = pid
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 970.0}
        mock_ws.broadcast = AsyncMock()
        db = _mock_db_for_close(trade)

        profile = _capped_profile(pid, per_lot_loss_stop=3000.0)
        with patch("app.services.yolo_profile_service.get_active_profiles_sync",
                   return_value=[profile]):
            from app.agent.trade_monitor import _check_position
            action = await _check_position(db, pos, yolo_mode=True)

        assert action is not None
        assert action["action_type"] == AgentActionType.PER_LOT_STOP_CLOSE.value
        assert trade.exit_reason == ExitReason.PER_LOT_STOP.value
        db.delete.assert_called_once_with(pos)

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_per_lot_stop_not_breached_keeps_position(self, mock_price, mock_ws):
        pid = uuid.uuid4()
        pos = _make_position(
            entry_price=Decimal("1000"), stop_loss=Decimal("960"),
            target_price=Decimal("1080"), lots=2, quantity=200,
            strategy_name=StrategyName.INTRADAY_FUTURES.value,
            fyers_option_symbol="NSE:VEDL26JUNFUT", symbol="VEDL",
        )
        pos.instrument_type = "FUTURE"
        pos.option_type = ""
        pos.yolo_profile_id = pid
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 990.0}  # -10/pt → -1000/lot, above the -3000 stop
        mock_ws.broadcast = AsyncMock()
        db = _mock_db_for_close(trade)

        profile = _capped_profile(pid, per_lot_loss_stop=3000.0)
        with patch("app.services.yolo_profile_service.get_active_profiles_sync",
                   return_value=[profile]):
            from app.agent.trade_monitor import _check_position
            action = await _check_position(db, pos, yolo_mode=True)

        assert action is None
        db.delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.ws_manager")
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    async def test_no_per_lot_stop_when_profile_unset(self, mock_price, mock_ws):
        pid = uuid.uuid4()
        pos = _make_position(
            entry_price=Decimal("1000"), stop_loss=Decimal("960"),
            target_price=Decimal("1080"), lots=2, quantity=200,
            strategy_name=StrategyName.INTRADAY_FUTURES.value,
            fyers_option_symbol="NSE:VEDL26JUNFUT", symbol="VEDL",
        )
        pos.instrument_type = "FUTURE"
        pos.option_type = ""
        pos.yolo_profile_id = pid
        trade = _make_trade(pos)
        mock_price.return_value = {"ltp": 970.0}  # would breach a 3000 stop, but none is set
        mock_ws.broadcast = AsyncMock()
        db = _mock_db_for_close(trade)

        profile = _capped_profile(pid, per_lot_loss_stop=None)
        with patch("app.services.yolo_profile_service.get_active_profiles_sync",
                   return_value=[profile]):
            from app.agent.trade_monitor import _check_position
            action = await _check_position(db, pos, yolo_mode=True)

        assert action is None


class TestDailyLossCap:
    """Per-profile daily loss cap in _check_pnl_caps (symmetric twin of the profit cap)."""

    @pytest.mark.asyncio
    @patch("app.agent.trade_monitor.notify_loss_cap_halt", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.notify_profit_cap_halt", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor._unrealized_net_pnl", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
    @patch("app.agent.trade_monitor.ws_manager")
    async def test_loss_cap_closes_positions_and_notifies(
        self, mock_ws, mock_price, mock_unreal, mock_profit_notify, mock_loss_notify,
    ):
        pid = uuid.uuid4()
        profile = _capped_profile(pid, profit_cap=10000.0, loss_cap=20000.0, name="20K")
        pos = _make_position(symbol="VEDL", fyers_option_symbol="NSE:VEDL26JUNFUT")
        pos.yolo_profile_id = pid
        trade = _make_trade(pos)

        # realized net -22000 (gross -22000), unrealized 0 → total -22000 <= -20000 → loss cap
        realized_result = MagicMock()
        realized_result.one.return_value = (Decimal("-22000"), Decimal("-22000"))
        positions_result = MagicMock()
        positions_result.scalars.return_value.all.return_value = [pos]
        trade_result = MagicMock()
        trade_result.scalar_one_or_none.return_value = trade

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[realized_result, positions_result, trade_result])
        db.add = MagicMock()
        db.delete = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        mock_unreal.return_value = 0.0
        mock_price.return_value = {"ltp": 100.0}
        mock_ws.broadcast = AsyncMock()

        with patch("app.services.yolo_profile_service.get_active_profiles",
                   new_callable=AsyncMock, return_value=[profile]), \
             patch("app.services.yolo_profile_service.get_uncapped_profile_ids",
                   new_callable=AsyncMock, return_value={pid}):
            from app.agent.trade_monitor import _check_pnl_caps
            actions = await _check_pnl_caps(db)

        assert actions and len(actions) == 1
        assert trade.exit_reason == ExitReason.LOSS_CAP.value
        mock_loss_notify.assert_called_once()
        mock_profit_notify.assert_not_called()
