"""Tests for telegram_commands.py — Telegram bot command handlers."""

import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import IST


# ── Factories ──────────────────────────────────────────────────────────────────

def _make_position(**overrides):
    p = MagicMock()
    p.symbol = overrides.get("symbol", "VEDL")
    p.strike_price = Decimal(overrides.get("strike_price", "0"))
    p.option_type = overrides.get("option_type", "FU")
    p.entry_price = Decimal(overrides.get("entry_price", "385.00"))
    p.target_price = Decimal(overrides.get("target_price", "400.00"))
    p.current_price = Decimal(overrides.get("current_price", "392.00"))
    p.unrealized_pnl = Decimal(overrides.get("unrealized_pnl", "1750.00"))
    p.opened_at = overrides.get("opened_at", datetime(2026, 5, 22, 9, 45, tzinfo=IST))
    p.is_shadow = overrides.get("is_shadow", True)
    p.trade_id = overrides.get("trade_id", uuid.uuid4())
    return p


def _make_trade(**overrides):
    t = MagicMock()
    t.symbol = overrides.get("symbol", "NIFTY")
    t.strike_price = Decimal(overrides.get("strike_price", "23000"))
    t.option_type = overrides.get("option_type", "CE")
    t.entry_price = Decimal(overrides.get("entry_price", "280.00"))
    t.target_price = Decimal(overrides.get("target_price", "400.00"))
    t.exit_price = Decimal(overrides.get("exit_price", "350.00"))
    t.pnl = Decimal(overrides.get("pnl", "5250.00"))
    t.pnl_percent = Decimal(overrides.get("pnl_percent", "25.0"))
    t.exit_reason = overrides.get("exit_reason", "AGENT_PROFIT")
    t.exit_time = overrides.get("exit_time", datetime(2026, 5, 22, 11, 0, tzinfo=IST))
    t.entry_time = overrides.get("entry_time", datetime(2026, 5, 22, 9, 30, tzinfo=IST))
    t.source = overrides.get("source", "SHADOW")
    t.status = overrides.get("status", "CLOSED")
    t.strategy_name = overrides.get("strategy_name", "vwap_pullback")
    return t


def _make_signal(**overrides):
    s = MagicMock()
    s.id = overrides.get("id", uuid.uuid4())
    s.symbol = overrides.get("symbol", "VEDL")
    s.signal_type = overrides.get("signal_type", "BUY_FUT")
    s.confidence = Decimal(overrides.get("confidence", "78.00"))
    s.status = overrides.get("status", "PENDING")
    s.strategy_name = overrides.get("strategy_name", "intraday_futures")
    s.generated_at = overrides.get("generated_at", datetime(2026, 5, 22, 10, 15, tzinfo=IST))
    return s


def _make_cfg(**overrides):
    cfg = MagicMock()
    cfg.autonomy_level = overrides.get("autonomy_level", "YOLO")
    cfg.min_confidence_for_execution = overrides.get("min_confidence_for_execution", 70)
    cfg.paper_trading = overrides.get("paper_trading", True)
    cfg.capital = overrides.get("capital", 1000000)
    return cfg


def _mock_session_two_queries(mock_factory, result1, result2):
    """Set up a mock session returning two sequential query results."""
    session = AsyncMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    exec_results = []
    for items in [result1, result2]:
        r = MagicMock()
        r.scalars.return_value.all.return_value = items
        exec_results.append(r)

    session.execute = AsyncMock(side_effect=exec_results)
    return session


# Patch paths: lazy imports inside handlers resolve to the source module,
# so we patch there. _send_trade_report calls send_telegram via its own
# lazy import from app.agent.notification.
SEND = "app.agent.notification.send_telegram"
SESSION = "app.core.database.async_session_factory"
REDIS = "app.core.redis.get_redis"
CFG = "app.services.trading_config.get_trading_config"
PARAMS = "app.services.strategy_params.get_strategy_params"
AGENT = "app.agent.agent_runner.agent_runner"
FEED = "app.data_feed.feed_manager.feed_manager"
MARKET_OPEN = "app.core.utils.is_market_open"
NOW_IST = "app.core.utils.now_ist"
GLOBAL_CUES = "app.services.morning_screener.get_global_cues"
BRIEFING = "app.services.morning_screener.get_morning_briefing"


# ── /shadow tests ──────────────────────────────────────────────────────────────

class TestHandleShadow:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_shadow_empty_day(self, mock_factory, mock_send):
        _mock_session_two_queries(mock_factory, [], [])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        assert mock_send.call_count == 1
        msg = mock_send.call_args_list[0][0][0]
        assert "Shadow PnL" in msg
        assert "<b>0</b>" in msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_shadow_with_open_and_closed(self, mock_factory, mock_send):
        pos = _make_position(unrealized_pnl="1750.00")
        trade = _make_trade(pnl="5250.00", exit_reason="AGENT_PROFIT")
        _mock_session_two_queries(mock_factory, [pos], [trade])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        assert mock_send.call_count == 3
        summary = mock_send.call_args_list[0][0][0]
        assert "Shadow PnL" in summary
        assert "1 open" in summary
        assert "1 closed" in summary

        open_msg = mock_send.call_args_list[1][0][0]
        assert "OPEN" in open_msg
        assert "VEDL" in open_msg
        assert "₹1,750" in open_msg

        closed_msg = mock_send.call_args_list[2][0][0]
        assert "CLOSED" in closed_msg
        assert "NIFTY 23000 CE" in closed_msg
        assert "TARGET" in closed_msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_shadow_no_pre_tags(self, mock_factory, mock_send):
        """Phone-friendly format: no <pre> monospace blocks."""
        pos = _make_position()
        trade = _make_trade()
        _mock_session_two_queries(mock_factory, [pos], [trade])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        for call in mock_send.call_args_list:
            assert "<pre>" not in call[0][0]

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_shadow_emoji_indicators(self, mock_factory, mock_send):
        """Positive PnL gets 🟢, negative gets 🔴."""
        winner = _make_trade(pnl="5000.00")
        loser = _make_trade(pnl="-3000.00", symbol="BANKNIFTY", exit_reason="AGENT_SL")
        _mock_session_two_queries(mock_factory, [], [winner, loser])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        closed_msg = mock_send.call_args_list[1][0][0]
        assert "🟢" in closed_msg
        assert "🔴" in closed_msg


# ── /yolo tests ────────────────────────────────────────────────────────────────

class TestHandleYolo:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_yolo_empty(self, mock_factory, mock_send):
        _mock_session_two_queries(mock_factory, [], [])

        from app.agent.telegram_commands import handle_yolo
        await handle_yolo("123")

        assert mock_send.call_count == 1
        assert "YOLO PnL" in mock_send.call_args_list[0][0][0]

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_yolo_with_trades(self, mock_factory, mock_send):
        pos = _make_position(is_shadow=False)
        trade = _make_trade(source="YOLO", pnl="2000.00")
        _mock_session_two_queries(mock_factory, [pos], [trade])

        from app.agent.telegram_commands import handle_yolo
        await handle_yolo("123")

        assert mock_send.call_count == 3
        assert "YOLO PnL" in mock_send.call_args_list[0][0][0]


# ── /status tests ──────────────────────────────────────────────────────────────

class TestHandleStatus:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    @patch(REDIS)
    @patch(PARAMS, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(FEED)
    @patch(AGENT)
    @patch(MARKET_OPEN, return_value=True)
    @patch(NOW_IST)
    async def test_status_market_open(
        self, mock_now, mock_market, mock_agent, mock_feed,
        mock_cfg, mock_params, mock_redis, mock_factory, mock_send
    ):
        now = datetime(2026, 5, 22, 14, 43, tzinfo=IST)
        mock_now.return_value = now
        mock_agent.is_running = True
        mock_cfg.return_value = _make_cfg()
        mock_feed._last_tick_at = datetime(2026, 5, 22, 14, 43, tzinfo=IST)
        mock_params.return_value = {"trading_windows": [], "dead_zone": None}

        r = AsyncMock()
        r.get = AsyncMock(return_value="MORNING_ACTIVE")
        mock_redis.return_value = r

        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        sig_result = MagicMock()
        sig_result.scalar.return_value = 8
        open_result = MagicMock()
        open_result.scalar.return_value = 2
        closed_result = MagicMock()
        closed_result.one.return_value = (3, Decimal("5200"))
        session.execute = AsyncMock(side_effect=[sig_result, open_result, closed_result])

        from app.agent.telegram_commands import handle_status
        await handle_status("123")

        assert mock_send.call_count == 1
        msg = mock_send.call_args_list[0][0][0]
        assert "Status" in msg
        assert "<b>OPEN</b>" in msg
        assert "min left" in msg
        assert "Running" in msg
        assert "YOLO" in msg
        assert "Live" in msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    @patch(REDIS)
    @patch(PARAMS, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(FEED)
    @patch(AGENT)
    @patch(MARKET_OPEN, return_value=False)
    @patch(NOW_IST)
    async def test_status_market_closed(
        self, mock_now, mock_market, mock_agent, mock_feed,
        mock_cfg, mock_params, mock_redis, mock_factory, mock_send
    ):
        now = datetime(2026, 5, 22, 18, 0, tzinfo=IST)
        mock_now.return_value = now
        mock_agent.is_running = False
        mock_cfg.return_value = _make_cfg(autonomy_level="MANUAL")
        mock_feed._last_tick_at = None
        mock_params.return_value = {"trading_windows": [], "dead_zone": None}

        r = AsyncMock()
        r.get = AsyncMock(return_value=None)
        mock_redis.return_value = r

        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        sig_result = MagicMock()
        sig_result.scalar.return_value = 0
        open_result = MagicMock()
        open_result.scalar.return_value = 0
        closed_result = MagicMock()
        closed_result.one.return_value = (0, Decimal("0"))
        session.execute = AsyncMock(side_effect=[sig_result, open_result, closed_result])

        from app.agent.telegram_commands import handle_status
        await handle_status("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "<b>CLOSED</b>" in msg
        assert "min left" not in msg
        assert "Stopped" in msg
        assert "No ticks" in msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    @patch(REDIS)
    @patch(PARAMS, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(FEED)
    @patch(AGENT)
    @patch(MARKET_OPEN, return_value=True)
    @patch(NOW_IST)
    async def test_status_feed_stale(
        self, mock_now, mock_market, mock_agent, mock_feed,
        mock_cfg, mock_params, mock_redis, mock_factory, mock_send
    ):
        now = datetime(2026, 5, 22, 12, 0, tzinfo=IST)
        mock_now.return_value = now
        mock_agent.is_running = True
        mock_cfg.return_value = _make_cfg()
        mock_feed._last_tick_at = datetime(2026, 5, 22, 11, 57, tzinfo=IST)
        mock_params.return_value = {"trading_windows": [], "dead_zone": None}

        r = AsyncMock()
        r.get = AsyncMock(return_value=None)
        mock_redis.return_value = r

        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        sig_result = MagicMock()
        sig_result.scalar.return_value = 0
        open_result = MagicMock()
        open_result.scalar.return_value = 0
        closed_result = MagicMock()
        closed_result.one.return_value = (0, Decimal("0"))
        session.execute = AsyncMock(side_effect=[sig_result, open_result, closed_result])

        from app.agent.telegram_commands import handle_status
        await handle_status("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "<b>Stale</b>" in msg


# ── /market tests ──────────────────────────────────────────────────────────────

class TestHandleMarket:

    @pytest.mark.asyncio
    @patch(BRIEFING, new_callable=AsyncMock)
    @patch(GLOBAL_CUES, new_callable=AsyncMock)
    @patch(SEND, new_callable=AsyncMock)
    @patch(REDIS)
    async def test_market_with_all_data(self, mock_redis, mock_send, mock_cues, mock_brief):
        import json
        r = AsyncMock()
        r.get = AsyncMock(side_effect=[
            json.dumps({"ltp": 23450, "change_pct": 0.45}),
            json.dumps({"ltp": 49120, "change_pct": -0.12}),
            json.dumps({"ltp": 14.2}),
        ])
        mock_redis.return_value = r

        mock_cues.return_value = {
            "global_score": 0.35, "overnight_bias": "BULLISH",
            "dow_futures_pct": 0.3, "crude_pct": -1.2, "dxy_pct": 0.1,
        }
        mock_brief.return_value = {"approach": "aggressive"}

        from app.agent.telegram_commands import handle_market
        await handle_market("123")

        assert mock_send.call_count == 1
        msg = mock_send.call_args_list[0][0][0]
        assert "Market" in msg
        assert "23,450" in msg
        assert "49,120" in msg
        assert "14.2" in msg
        assert "0.35" in msg
        assert "BULLISH" in msg
        assert "aggressive" in msg

    @pytest.mark.asyncio
    @patch(BRIEFING, new_callable=AsyncMock)
    @patch(GLOBAL_CUES, new_callable=AsyncMock)
    @patch(SEND, new_callable=AsyncMock)
    @patch(REDIS)
    async def test_market_missing_redis_keys(self, mock_redis, mock_send, mock_cues, mock_brief):
        r = AsyncMock()
        r.get = AsyncMock(return_value=None)
        mock_redis.return_value = r

        mock_cues.return_value = None
        mock_brief.return_value = None

        from app.agent.telegram_commands import handle_market
        await handle_market("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "—" in msg


# ── /signals tests ─────────────────────────────────────────────────────────────

class TestHandleSignals:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_signals_empty(self, mock_factory, mock_cfg, mock_send):
        mock_cfg.return_value = _make_cfg()
        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        from app.agent.telegram_commands import handle_signals
        await handle_signals("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "No actionable signals" in msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_signals_grouped_by_status(self, mock_factory, mock_cfg, mock_send):
        mock_cfg.return_value = _make_cfg()
        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        pending_sig = _make_signal(status="PENDING", symbol="VEDL", confidence="78")
        executed_sig = _make_signal(status="EXECUTED", symbol="RELIANCE", confidence="85")
        result = MagicMock()
        result.scalars.return_value.all.return_value = [pending_sig, executed_sig]
        session.execute = AsyncMock(return_value=result)

        from app.agent.telegram_commands import handle_signals
        await handle_signals("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "PENDING" in msg
        assert "EXECUTED" in msg
        assert "VEDL" in msg
        assert "RELIANCE" in msg
        assert "1 pending" in msg
        assert "1 executed" in msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(CFG, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_signals_strategy_short_labels(self, mock_factory, mock_cfg, mock_send):
        mock_cfg.return_value = _make_cfg()
        session = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        s5_sig = _make_signal(strategy_name="intraday_futures")
        s2_sig = _make_signal(strategy_name="vwap_pullback", signal_type="BUY_CE", symbol="NIFTY")
        result = MagicMock()
        result.scalars.return_value.all.return_value = [s5_sig, s2_sig]
        session.execute = AsyncMock(return_value=result)

        from app.agent.telegram_commands import handle_signals
        await handle_signals("123")

        msg = mock_send.call_args_list[0][0][0]
        assert "S5" in msg
        assert "S2" in msg


# ── /help tests ────────────────────────────────────────────────────────────────

class TestHandleHelp:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    async def test_help_lists_all_commands(self, mock_send):
        from app.agent.telegram_commands import handle_help
        await handle_help("123")

        msg = mock_send.call_args_list[0][0][0]
        for cmd in ["/status", "/market", "/shadow", "/yolo", "/signals", "/help"]:
            assert cmd in msg


# ── Dispatch table tests ──────────────────────────────────────────────────────

class TestDispatch:

    def test_all_commands_registered(self):
        from app.agent.telegram_commands import _HANDLERS
        expected = {"status", "market", "shadow", "yolo", "signals", "help"}
        assert set(_HANDLERS.keys()) == expected

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    async def test_unknown_command_ignored(self, mock_send):
        from app.agent.telegram_commands import handle_command
        await handle_command("nonexistent", "123")
        mock_send.assert_not_called()


# ── Helper function tests ─────────────────────────────────────────────────────

class TestHelpers:

    def test_pnl_emoji_positive(self):
        from app.agent.telegram_commands import _pnl_emoji
        assert _pnl_emoji(100) == "🟢"
        assert _pnl_emoji(0) == "🟢"
        assert _pnl_emoji(Decimal("5000")) == "🟢"

    def test_pnl_emoji_negative(self):
        from app.agent.telegram_commands import _pnl_emoji
        assert _pnl_emoji(-100) == "🔴"
        assert _pnl_emoji(Decimal("-1")) == "🔴"

    def test_pnl_emoji_none(self):
        from app.agent.telegram_commands import _pnl_emoji
        assert _pnl_emoji(None) == "🟢"

    def test_pct_str(self):
        from app.agent.telegram_commands import _pct_str
        assert _pct_str(25.0) == "+25.0%"
        assert _pct_str(-30.5) == "-30.5%"
        assert _pct_str(0) == "+0.0%"
        assert _pct_str(None) == "+0.0%"

    def test_strategy_short(self):
        from app.agent.telegram_commands import _strategy_short
        assert _strategy_short("intraday_futures") == "S5"
        assert _strategy_short("vwap_pullback") == "S2"
        assert _strategy_short("can_slim") == "S4"
        assert _strategy_short("orb") == "S1"
        assert _strategy_short("unknown_long") == "unknow"

    def test_instrument_label_option(self):
        from app.agent.telegram_commands import _instrument_label
        assert _instrument_label("NIFTY", 23000, "CE") == "NIFTY 23000 CE"
        assert _instrument_label("BANKNIFTY", Decimal("48000.00"), "PE") == "BANKNIFTY 48000 PE"

    def test_instrument_label_futures(self):
        from app.agent.telegram_commands import _instrument_label
        assert _instrument_label("VEDL", 0, "FU") == "VEDL"
        assert _instrument_label("TCS", 0, None) == "TCS"

    def test_to_ist_converts_utc(self):
        from app.agent.telegram_commands import _to_ist
        from zoneinfo import ZoneInfo
        utc_dt = datetime(2026, 5, 22, 4, 15, tzinfo=ZoneInfo("UTC"))
        assert _to_ist(utc_dt) == "09:45"

    def test_to_ist_none(self):
        from app.agent.telegram_commands import _to_ist
        assert _to_ist(None) == "—"

    def test_to_ist_already_ist(self):
        from app.agent.telegram_commands import _to_ist
        ist_dt = datetime(2026, 5, 22, 10, 30, tzinfo=IST)
        assert _to_ist(ist_dt) == "10:30"

    def test_direction_long(self):
        from app.agent.telegram_commands import _direction
        assert _direction(Decimal("385"), Decimal("400")) == "LONG"

    def test_direction_short(self):
        from app.agent.telegram_commands import _direction
        assert _direction(Decimal("385"), Decimal("370")) == "SHORT"

    def test_direction_none(self):
        from app.agent.telegram_commands import _direction
        assert _direction(None, Decimal("400")) == ""
        assert _direction(Decimal("385"), None) == ""


class TestDirectionInCards:

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_futures_position_shows_long(self, mock_factory, mock_send):
        pos = _make_position(option_type="FU", target_price="400.00")
        _mock_session_two_queries(mock_factory, [pos], [])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        open_msg = mock_send.call_args_list[1][0][0]
        assert "LONG" in open_msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_futures_position_shows_short(self, mock_factory, mock_send):
        pos = _make_position(option_type="FU", target_price="370.00")
        _mock_session_two_queries(mock_factory, [pos], [])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        open_msg = mock_send.call_args_list[1][0][0]
        assert "SHORT" in open_msg

    @pytest.mark.asyncio
    @patch(SEND, new_callable=AsyncMock)
    @patch(SESSION)
    async def test_option_hides_direction(self, mock_factory, mock_send):
        pos = _make_position(option_type="CE", symbol="NIFTY", strike_price="23000",
                             target_price="400.00")
        _mock_session_two_queries(mock_factory, [pos], [])

        from app.agent.telegram_commands import handle_shadow
        await handle_shadow("123")

        open_msg = mock_send.call_args_list[1][0][0]
        assert "LONG" not in open_msg
        assert "SHORT" not in open_msg
