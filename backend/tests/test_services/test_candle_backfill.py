"""Tests for candle_backfill._persist_candles session + trading-day guard.

_persist_candles (deep-history + WS-reconnect gap backfill) must never write a
row outside a real 09:15-15:30 IST session — an off-session/holiday row poisons
_query_previous_day and can block a strategy the next trading day.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

import pytest

IST = ZoneInfo("Asia/Kolkata")


def _candle(dt: datetime) -> dict:
    """Build a raw Fyers-shaped candle dict (epoch timestamp) for the given IST time."""
    return {
        "timestamp": int(dt.timestamp()),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
    }


class TestPersistCandlesGuard:
    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    @patch("app.core.utils.is_trading_day", return_value=False)
    async def test_skips_non_trading_day(self, _trading, mock_factory):
        """In-session time but a non-trading day → dropped, no DB write."""
        from app.services.candle_backfill import _persist_candles

        n = await _persist_candles("NIFTY", [_candle(datetime(2026, 5, 28, 11, 0, tzinfo=IST))])
        assert n == 0
        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    @patch("app.core.utils.is_trading_day", return_value=True)
    async def test_skips_off_session_time(self, _trading, mock_factory):
        """Trading day but 17:44 IST is post-close → dropped, no DB write."""
        from app.services.candle_backfill import _persist_candles

        n = await _persist_candles("NIFTY", [_candle(datetime(2026, 5, 29, 17, 44, tzinfo=IST))])
        assert n == 0
        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    @patch("app.core.utils.is_trading_day", return_value=True)
    async def test_persists_valid_in_session_candle(self, _trading, mock_factory):
        """Trading day + in-session time → persisted."""
        from app.services.candle_backfill import _persist_candles

        session = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        cm.__aexit__.return_value = None
        mock_factory.return_value = cm

        n = await _persist_candles("NIFTY", [_candle(datetime(2026, 5, 29, 11, 0, tzinfo=IST))])
        assert n == 1
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
