"""Tests for per-setup win rates in morning briefing data gathering."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_trade(symbol: str, pnl: float, signal_id: str, entry_time: datetime | None = None):
    t = MagicMock()
    t.symbol = symbol
    t.pnl = pnl
    t.signal_id = signal_id
    t.entry_time = entry_time or datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc)
    t.entry_price = 100.0
    t.exit_price = 110.0 if pnl > 0 else 90.0
    t.pnl_percent = abs(pnl) / 100
    t.exit_reason = "TARGET_HIT" if pnl > 0 else "STOP_LOSS"
    t.status = "CLOSED"
    t.side = "BUY_FUT"
    return t


class TestBriefingPerSetupWinRates:
    """Per-setup win rates: _gather_briefing_data joins Trade→Signal to get setup_type."""

    @pytest.mark.asyncio
    @patch("app.services.morning_screener.get_redis")
    @patch("app.services.morning_screener.get_global_cues", new_callable=AsyncMock, return_value={})
    @patch("app.services.morning_screener._summarize_agent_log", new_callable=AsyncMock, return_value={"available": False})
    async def test_extracts_setup_type_from_signal_indicators(self, _log, _cues, mock_get_redis):
        from app.services.morning_screener import _gather_briefing_data

        trades = [
            _make_trade("ADANI", 5000, "sig-1"),
            _make_trade("SBIN", -2000, "sig-2"),
            _make_trade("TCS", 3000, "sig-3"),
        ]

        signal_rows = [
            ("sig-1", {"setup_type": "ORB"}),
            ("sig-2", {"setup_type": "VWAP_BOUNCE"}),
            ("sig-3", {"setup_type": "ORB"}),
        ]

        mock_session = AsyncMock()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = []
            elif call_count == 2:
                result.scalars.return_value.all.return_value = trades
            elif call_count == 3:
                result.all.return_value = []
            elif call_count == 4:
                result.all.return_value = signal_rows
            return result

        mock_session.execute = mock_execute

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch("app.core.database.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            data = await _gather_briefing_data(date(2026, 4, 27))

        setup_stats = data["setup_stats_5d"]
        assert "ORB" in setup_stats
        assert setup_stats["ORB"]["wins"] == 2
        assert setup_stats["ORB"]["losses"] == 0
        assert setup_stats["ORB"]["win_rate"] == 100.0

        assert "VWAP_BOUNCE" in setup_stats
        assert setup_stats["VWAP_BOUNCE"]["wins"] == 0
        assert setup_stats["VWAP_BOUNCE"]["losses"] == 1
        assert setup_stats["VWAP_BOUNCE"]["win_rate"] == 0.0

    @pytest.mark.asyncio
    @patch("app.services.morning_screener.get_redis")
    @patch("app.services.morning_screener.get_global_cues", new_callable=AsyncMock, return_value={})
    @patch("app.services.morning_screener._summarize_agent_log", new_callable=AsyncMock, return_value={"available": False})
    async def test_defaults_to_orb_when_no_signal_indicators(self, _log, _cues, mock_get_redis):
        """Trades without signal_id or without setup_type in indicators default to ORB."""
        from app.services.morning_screener import _gather_briefing_data

        trades = [
            _make_trade("ADANI", 5000, "sig-1"),
            _make_trade("SBIN", -2000, None),  # no signal_id
        ]

        signal_rows = [
            ("sig-1", {}),  # no setup_type key
        ]

        mock_session = AsyncMock()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = []
            elif call_count == 2:
                result.scalars.return_value.all.return_value = trades
            elif call_count == 3:
                result.all.return_value = []
            elif call_count == 4:
                result.all.return_value = signal_rows
            return result

        mock_session.execute = mock_execute

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch("app.core.database.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            data = await _gather_briefing_data(date(2026, 4, 27))

        setup_stats = data["setup_stats_5d"]
        assert "ORB" in setup_stats
        assert setup_stats["ORB"]["wins"] == 1
        assert setup_stats["ORB"]["losses"] == 1

    @pytest.mark.asyncio
    @patch("app.services.morning_screener.get_redis")
    @patch("app.services.morning_screener.get_global_cues", new_callable=AsyncMock, return_value={})
    @patch("app.services.morning_screener._summarize_agent_log", new_callable=AsyncMock, return_value={"available": False})
    async def test_empty_trades_produces_empty_setup_stats(self, _log, _cues, mock_get_redis):
        from app.services.morning_screener import _gather_briefing_data

        mock_session = AsyncMock()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count <= 2:
                result.scalars.return_value.all.return_value = []
            else:
                result.all.return_value = []
            return result

        mock_session.execute = mock_execute

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch("app.core.database.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            data = await _gather_briefing_data(date(2026, 4, 27))

        assert data["setup_stats_5d"] == {}

    @pytest.mark.asyncio
    @patch("app.services.morning_screener.get_redis")
    @patch("app.services.morning_screener.get_global_cues", new_callable=AsyncMock, return_value={})
    @patch("app.services.morning_screener._summarize_agent_log", new_callable=AsyncMock, return_value={"available": False})
    async def test_win_rate_calculation_accuracy(self, _log, _cues, mock_get_redis):
        """Win rate should be wins / (wins + losses) * 100, rounded to 1 decimal."""
        from app.services.morning_screener import _gather_briefing_data

        trades = [
            _make_trade("A", 100, "s1"),
            _make_trade("B", 200, "s2"),
            _make_trade("C", -50, "s3"),
        ]

        signal_rows = [
            ("s1", {"setup_type": "PDH_PDL"}),
            ("s2", {"setup_type": "PDH_PDL"}),
            ("s3", {"setup_type": "PDH_PDL"}),
        ]

        mock_session = AsyncMock()
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = []
            elif call_count == 2:
                result.scalars.return_value.all.return_value = trades
            elif call_count == 3:
                result.all.return_value = []
            elif call_count == 4:
                result.all.return_value = signal_rows
            return result

        mock_session.execute = mock_execute

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        with patch("app.core.database.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            data = await _gather_briefing_data(date(2026, 4, 27))

        pdh = data["setup_stats_5d"]["PDH_PDL"]
        assert pdh["wins"] == 2
        assert pdh["losses"] == 1
        assert pdh["win_rate"] == 66.7
