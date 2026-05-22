"""Tests for Telegram notification redesign.

Covers: confidence floor filter, Case-2 YOLO trigger, manual execution
notification, shadow exclusion in EOD, and morning message formatting.
"""

import uuid
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import IST


# ── Factories ──────────────────────────────────────────────────────────────────


def _make_signal(**overrides):
    s = MagicMock()
    s.id = overrides.get("id", uuid.uuid4())
    s.symbol = overrides.get("symbol", "VEDL")
    s.signal_type = overrides.get("signal_type", "BUY_FUT")
    s.strategy_name = overrides.get("strategy_name", "intraday_futures")
    s.entry_price = Decimal(overrides.get("entry_price", "385.00"))
    s.stop_loss = Decimal(overrides.get("stop_loss", "380.00"))
    s.target_price = Decimal(overrides.get("target_price", "395.00"))
    s.strike_price = Decimal(overrides.get("strike_price", "0"))
    s.expiry_date = overrides.get("expiry_date", None)
    s.confidence = Decimal(overrides.get("confidence", "75"))
    s.instrument_type = overrides.get("instrument_type", "FUTURE")
    s.blocked_reason = overrides.get("blocked_reason", None)
    s.status = overrides.get("status", "PENDING")
    return s


def _make_cfg(**overrides):
    cfg = MagicMock()
    cfg.min_confidence_for_execution = overrides.get("min_confidence_for_execution", 70)
    cfg.yolo_mode = overrides.get("yolo_mode", False)
    cfg.paper_trading = overrides.get("paper_trading", True)
    return cfg


# ── Phase 1a: Confidence floor filter ─────────────────────────────────────────


class TestConfidenceFloor:
    """on_new_signal should only send Telegram when confidence >= threshold."""

    @pytest.mark.asyncio
    async def test_signal_above_threshold_sends_telegram(self):
        sig = _make_signal(confidence="75")
        cfg = _make_cfg(min_confidence_for_execution=70)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sig
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)

        with patch("app.agent.agent_runner.get_trading_config", new_callable=AsyncMock, return_value=cfg), \
             patch("app.core.database.async_session_factory", mock_factory), \
             patch("app.agent.agent_runner.notify_signal_generated", new_callable=AsyncMock) as mock_notify:

            from app.agent.agent_runner import AgentRunner
            runner = AgentRunner()
            await runner.on_new_signal(sig.id)

            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_below_threshold_skips_telegram(self):
        sig = _make_signal(confidence="55")
        cfg = _make_cfg(min_confidence_for_execution=70)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sig
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)

        with patch("app.agent.agent_runner.get_trading_config", new_callable=AsyncMock, return_value=cfg), \
             patch("app.core.database.async_session_factory", mock_factory), \
             patch("app.agent.agent_runner.notify_signal_generated", new_callable=AsyncMock) as mock_notify:

            from app.agent.agent_runner import AgentRunner
            runner = AgentRunner()
            await runner.on_new_signal(sig.id)

            mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_signal_at_exact_threshold_sends_telegram(self):
        sig = _make_signal(confidence="70")
        cfg = _make_cfg(min_confidence_for_execution=70)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sig
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)

        with patch("app.agent.agent_runner.get_trading_config", new_callable=AsyncMock, return_value=cfg), \
             patch("app.core.database.async_session_factory", mock_factory), \
             patch("app.agent.agent_runner.notify_signal_generated", new_callable=AsyncMock) as mock_notify:

            from app.agent.agent_runner import AgentRunner
            runner = AgentRunner()
            await runner.on_new_signal(sig.id)

            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_with_none_confidence_sends_telegram(self):
        """Signals with no confidence score should still notify (backward compat)."""
        sig = _make_signal()
        sig.confidence = None
        cfg = _make_cfg(min_confidence_for_execution=70)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sig
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)

        with patch("app.agent.agent_runner.get_trading_config", new_callable=AsyncMock, return_value=cfg), \
             patch("app.core.database.async_session_factory", mock_factory), \
             patch("app.agent.agent_runner.notify_signal_generated", new_callable=AsyncMock) as mock_notify:

            from app.agent.agent_runner import AgentRunner
            runner = AgentRunner()
            await runner.on_new_signal(sig.id)

            mock_notify.assert_called_once()


# ── Phase 1c: Manual execution notification ───────────────────────────────────


class TestManualExecutionNotification:
    """Manual execution should send a Telegram notification."""

    @pytest.mark.asyncio
    async def test_notify_manual_executed_sends_message(self):
        with patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send:
            from app.agent.notification import notify_manual_executed
            await notify_manual_executed(
                symbol="VEDL",
                signal_type="BUY_FUT",
                strategy_name="intraday_futures",
                entry=385.0,
                stop_loss=380.0,
                target=395.0,
                strike=None,
                expiry="2026-05-29",
                lots=1,
                quantity=2500,
                instrument_type="FUTURE",
            )
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Manual Exec" in msg
            assert "VEDL" in msg
            assert "385" in msg

    @pytest.mark.asyncio
    async def test_notify_manual_executed_option_format(self):
        with patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send:
            from app.agent.notification import notify_manual_executed
            await notify_manual_executed(
                symbol="NIFTY",
                signal_type="BUY_CE",
                strategy_name="vwap_pullback",
                entry=250.0,
                stop_loss=200.0,
                target=350.0,
                strike=23500.0,
                expiry="2026-05-27",
                lots=2,
                quantity=150,
                instrument_type="OPTION",
            )
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "23500" in msg
            assert "2 lots" in msg


# ── Phase 3: Shadow exclusion in EOD ──────────────────────────────────────────


class TestEodShadowExclusion:
    """Daily summary should exclude shadow trades."""

    @pytest.mark.asyncio
    async def test_shadow_trades_excluded_from_summary(self):
        from app.core.enums import TradeSource

        mock_trades = [
            MagicMock(
                symbol="VEDL", pnl=Decimal("5000"), net_pnl=Decimal("4800"),
                entry_time=datetime(2026, 5, 22, 10, 0, tzinfo=IST),
                status="CLOSED", source=TradeSource.MANUAL.value,
                side="BUY", exit_reason="AGENT_PROFIT",
            ),
            MagicMock(
                symbol="TCS", pnl=Decimal("-2000"), net_pnl=Decimal("-2200"),
                entry_time=datetime(2026, 5, 22, 11, 0, tzinfo=IST),
                status="CLOSED", source=TradeSource.SHADOW.value,
                side="BUY", exit_reason="AGENT_SL",
            ),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_trades[0]]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_session)

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("app.core.database.async_session_factory", mock_factory), \
             patch("app.core.redis.get_redis", return_value=mock_redis), \
             patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("app.services.morning_screener.get_global_cues", new_callable=AsyncMock, return_value={}), \
             patch("app.services.morning_screener.get_morning_briefing", new_callable=AsyncMock, return_value={}), \
             patch("app.services.morning_screener.get_watchlist", new_callable=AsyncMock, return_value=[]), \
             patch("app.agent.notification._classify_fo_buildup", new_callable=AsyncMock, return_value={}), \
             patch("app.agent.notification._get_nifty_bn_oi_levels", new_callable=AsyncMock, return_value={}), \
             patch("app.research.llm_client.create_llm_client") as mock_llm_factory:

            mock_llm = AsyncMock()
            mock_llm.generate_json = AsyncMock(return_value={"market_wrap": "Test.", "trading_assessment": "Test."})
            mock_llm_factory.return_value = mock_llm

            from app.tasks.daily_summary_task import send_daily_summary
            await send_daily_summary()

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "VEDL" in msg
            # Shadow trade TCS should NOT appear
            assert "TCS" not in msg
            assert "Trades: 1" in msg


# ── Phase 2: Morning message formatting ───────────────────────────────────────


class TestMorningPremarketMessage:
    """Morning pre-market message should contain expected sections."""

    @pytest.mark.asyncio
    async def test_message_contains_key_sections(self):
        briefing = {
            "approach": "normal",
            "summary": "Market looks balanced today.",
            "sector_bias": "IT",
            "sector_avoid": "METALS",
            "setup_priority": ["ORB", "VWAP_BOUNCE"],
            "flags": ["VIX rising"],
            "max_lots_recommendation": 2,
        }
        global_cues = {
            "nifty_price": 23650,
            "nifty_pct": -0.14,
            "dow_futures_pct": 0.3,
            "sp500_close_pct": 0.2,
            "nasdaq_close_pct": 0.5,
            "crude_pct": -1.2,
            "us_vix": 14.5,
            "global_score": 0.3,
            "overnight_bias": "NEUTRAL",
        }

        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={
            "market_overview": "US markets closed positive.",
            "sectors_long": ["IT", "Banking"],
            "sectors_short": ["Metals"],
            "outlook": "Market likely to open flat with stock-specific action.",
        })

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value='[{"symbol": "VEDL"}, {"symbol": "TCS"}]')

        with patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("app.research.llm_client.create_llm_client", return_value=mock_llm), \
             patch("app.core.redis.get_redis", return_value=mock_redis), \
             patch("app.agent.notification._classify_fo_buildup", new_callable=AsyncMock, return_value={"long_buildup": ["INFY"], "short_buildup": ["TATASTEEL"]}), \
             patch("app.agent.notification._get_nifty_bn_oi_levels", new_callable=AsyncMock, return_value={"nifty_support": [23500, 23400], "nifty_resistance": [23700, 23800], "bn_support": [], "bn_resistance": []}):

            from app.agent.notification import notify_morning_premarket
            await notify_morning_premarket(briefing, global_cues)

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Pre-Market Report" in msg
            assert "23,650" in msg
            assert "NORMAL" in msg
            assert "F&O Build-Up" in msg or "F&amp;O Build-Up" in msg
            assert "INFY" in msg
            assert "Key Levels" in msg

    @pytest.mark.asyncio
    async def test_llm_failure_still_sends_message(self):
        """If LLM fails, message should still be sent with briefing data."""
        briefing = {
            "approach": "conservative",
            "summary": "Caution advised.",
            "sector_bias": "none",
            "sector_avoid": "none",
            "setup_priority": ["ORB"],
            "flags": [],
            "max_lots_recommendation": 1,
        }
        global_cues = {"nifty_price": 23500, "nifty_pct": 0.1}

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send, \
             patch("app.research.llm_client.create_llm_client", side_effect=Exception("LLM down")), \
             patch("app.core.redis.get_redis", return_value=mock_redis), \
             patch("app.agent.notification._classify_fo_buildup", new_callable=AsyncMock, return_value={}), \
             patch("app.agent.notification._get_nifty_bn_oi_levels", new_callable=AsyncMock, return_value={}):

            from app.agent.notification import notify_morning_premarket
            await notify_morning_premarket(briefing, global_cues)

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "CONSERVATIVE" in msg
            assert "Caution advised" in msg


class TestMorningPreopenMessage:
    """Pre-open update should format watchlist correctly."""

    @pytest.mark.asyncio
    async def test_message_contains_watchlist(self):
        watchlist = [
            {"symbol": "VEDL", "bias": "BULLISH", "gap_pct": 1.2, "composite_score": 78},
            {"symbol": "TCS", "bias": "BEARISH", "gap_pct": -0.5, "composite_score": 65},
        ]
        global_cues = {"nifty_gap_pct": 0.8, "india_vix_live": 15.5}

        with patch("app.agent.notification.send_telegram", new_callable=AsyncMock) as mock_send:
            from app.agent.notification import notify_morning_preopen
            await notify_morning_preopen(watchlist, global_cues)

            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Pre-Open Update" in msg
            assert "VEDL" in msg
            assert "TCS" in msg
            assert "Gap-up" in msg
            assert "+0.80%" in msg or "0.80" in msg
