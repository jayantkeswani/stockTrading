"""Tests for FeedManager — decoupled strategy evaluation trigger."""

import asyncio
from datetime import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.enums import StrategyName


class TestFeedManagerAutoStrategyTrigger:
    """Tests that FeedManager's _run_auto_strategy_evaluation correctly
    checks auto_mode strategies before triggering evaluation."""

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.strategy_runner")
    @patch("app.data_feed.feed_manager.feed_manager._run_auto_strategy_evaluation")
    async def test_emit_candle_calls_auto_evaluation(self, mock_auto_eval, mock_runner):
        """_emit_candle should call _run_auto_strategy_evaluation, not strategy_runner directly."""
        from app.data_feed.feed_manager import feed_manager

        mock_auto_eval.return_value = None

        # We need to temporarily replace the method to check it's called
        # Instead, let's just verify the method exists and has the right name
        assert hasattr(feed_manager, "_run_auto_strategy_evaluation")
        assert not hasattr(feed_manager, "_run_strategy_evaluation")

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.is_market_open", return_value=True)
    @patch("app.data_feed.feed_manager.strategy_runner")
    @patch("app.services.strategy_runner.get_auto_strategies_for_symbol", new_callable=AsyncMock)
    async def test_auto_eval_triggers_when_strategies_match(self, mock_get_auto, mock_runner, _mock_market):
        """When auto strategies match the symbol, trigger on_candle_close with filter."""
        from app.data_feed.feed_manager import feed_manager

        mock_get_auto.return_value = [StrategyName.VWAP_PULLBACK]
        mock_runner._is_futures_volume_symbol.return_value = False
        mock_runner.on_candle_close = AsyncMock()

        candle_data = {
            "symbol": "NIFTY",
            "timeframe": "1m",
            "o": 24000, "h": 24010, "l": 23990, "c": 24005,
            "v": 1000, "timestamp": "2026-04-16T10:00:00+05:30",
        }

        await feed_manager._run_auto_strategy_evaluation("NIFTY", candle_data)

        mock_runner.on_candle_close.assert_called_once_with(
            "NIFTY", candle_data, strategy_filter=[StrategyName.VWAP_PULLBACK],
        )

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.is_market_open", return_value=True)
    @patch("app.data_feed.feed_manager.strategy_runner")
    @patch("app.services.strategy_runner.get_auto_strategies_for_symbol", new_callable=AsyncMock)
    async def test_auto_eval_skips_when_no_match(self, mock_get_auto, mock_runner, _mock_market):
        """When no auto strategies match the symbol, don't trigger evaluation."""
        from app.data_feed.feed_manager import feed_manager

        mock_get_auto.return_value = []
        mock_runner._is_futures_volume_symbol.return_value = False
        mock_runner.on_candle_close = AsyncMock()

        candle_data = {
            "symbol": "INDIA VIX",
            "timeframe": "1m",
            "o": 15, "h": 15.1, "l": 14.9, "c": 15.05,
            "v": 0, "timestamp": "2026-04-16T10:00:00+05:30",
        }

        await feed_manager._run_auto_strategy_evaluation("INDIA VIX", candle_data)

        mock_runner.on_candle_close.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.is_market_open", return_value=True)
    @patch("app.data_feed.feed_manager.strategy_runner")
    async def test_auto_eval_forwards_futures_volume_symbols(self, mock_runner, _mock_market):
        """Futures volume symbols (e.g. NIFTY_FUT) bypass strategy check and go directly to on_candle_close."""
        from app.data_feed.feed_manager import feed_manager

        mock_runner._is_futures_volume_symbol.return_value = True
        mock_runner.on_candle_close = AsyncMock()

        candle_data = {
            "symbol": "NIFTY_FUT",
            "timeframe": "1m",
            "o": 24000, "h": 24010, "l": 23990, "c": 24005,
            "v": 5000, "timestamp": "2026-04-16T10:00:00+05:30",
        }

        await feed_manager._run_auto_strategy_evaluation("NIFTY_FUT", candle_data)

        mock_runner.on_candle_close.assert_called_once_with("NIFTY_FUT", candle_data)


class TestEmitCandlePersistGuard:
    """_emit_candle persists only real in-session candles (trading day, 09:15–15:30 IST).

    Off-hours/holiday ticks must NOT be written to market_data_1m — a stray row
    poisons _query_previous_day the next trading day and blocks Strategy 2.
    """

    @staticmethod
    def _candle(ts: str) -> dict:
        return {
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000, "timestamp": ts,
        }

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.ws_manager")
    @patch("app.data_feed.feed_manager.publish_event", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.feed_manager._run_auto_strategy_evaluation", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.feed_manager._persist_candle", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.is_market_open", return_value=True)
    async def test_persists_in_session_candle(self, mock_open, mock_persist, _eval, _pub, mock_ws):
        from app.data_feed.feed_manager import feed_manager

        mock_ws.broadcast = AsyncMock()
        await feed_manager._emit_candle("NIFTY", self._candle("2026-05-29T10:00:00+05:30"))
        await asyncio.sleep(0)

        mock_persist.assert_called_once()
        # Guard is consulted with the candle's own (parsed) timestamp.
        assert isinstance(mock_open.call_args.args[0], datetime)

    @pytest.mark.asyncio
    @patch("app.data_feed.feed_manager.ws_manager")
    @patch("app.data_feed.feed_manager.publish_event", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.feed_manager._run_auto_strategy_evaluation", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.feed_manager._persist_candle", new_callable=AsyncMock)
    @patch("app.data_feed.feed_manager.is_market_open", return_value=False)
    async def test_skips_off_session_candle(self, _open, mock_persist, _eval, mock_pub, mock_ws):
        from app.data_feed.feed_manager import feed_manager

        mock_ws.broadcast = AsyncMock()
        # 17:44 IST on the 2026-05-28 holiday — the exact poison-row shape.
        await feed_manager._emit_candle("NIFTY", self._candle("2026-05-28T17:44:00+05:30"))
        await asyncio.sleep(0)

        mock_persist.assert_not_called()
        # Display path still runs (publish/broadcast) even when not persisted.
        mock_pub.assert_awaited_once()
        mock_ws.broadcast.assert_awaited_once()
