"""Tests for NSE CM bhav copy task."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.nse_bhav_copy_task import (
    _build_bhav_url,
    _parse_bhav_csv,
    _previous_trading_day,
    fetch_bhav_copy,
    get_bhav_copy,
)


# ---------------------------------------------------------------------------
# Sample CSV content matching NSE bhav copy format
# ---------------------------------------------------------------------------

SAMPLE_CSV = """\
SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
RELIANCE, EQ, 25-APR-2026, 2440.00, 2450.00, 2480.00, 2430.00, 2465.00, 2470.00, 2460.00, 5000000, 123500.00, 150000, 2500000, 50.00
TCS, EQ, 25-APR-2026, 3490.00, 3500.00, 3550.00, 3480.00, 3510.00, 3520.00, 3510.00, 2000000, 70400.00, 80000, 1200000, 60.00
RELIANCE, BE, 25-APR-2026, 2440.00, 2450.00, 2480.00, 2430.00, 2465.00, 2470.00, 2460.00, 100000, 2470.00, 5000, 100000, 100.00
INFY, EQ, 25-APR-2026, 1395.00, 1400.00, 1420.00, 1390.00, 1405.00, 1410.00, 1405.00, 3000000, 42300.00, 100000, 900000, 30.00
"""


class TestParseBhavCsv:
    """Test CSV parsing logic."""

    def test_parses_eq_series_only(self):
        result = _parse_bhav_csv(SAMPLE_CSV)
        # RELIANCE BE series should be excluded
        assert "RELIANCE" in result
        assert len(result) == 3  # RELIANCE EQ, TCS EQ, INFY EQ

    def test_extracts_delivery_pct(self):
        result = _parse_bhav_csv(SAMPLE_CSV)
        assert result["RELIANCE"]["delivery_pct"] == 50.00
        assert result["TCS"]["delivery_pct"] == 60.00
        assert result["INFY"]["delivery_pct"] == 30.00

    def test_extracts_close(self):
        result = _parse_bhav_csv(SAMPLE_CSV)
        assert result["RELIANCE"]["close"] == 2470.00
        assert result["TCS"]["close"] == 3520.00

    def test_extracts_prev_close(self):
        result = _parse_bhav_csv(SAMPLE_CSV)
        assert result["RELIANCE"]["prev_close"] == 2440.00
        assert result["TCS"]["prev_close"] == 3490.00

    def test_empty_csv(self):
        csv_text = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        result = _parse_bhav_csv(csv_text)
        assert result == {}

    def test_invalid_numeric_skipped(self):
        csv_text = """\
SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
BADSTOCK, EQ, 25-APR-2026, 99.00, 100.00, 105.00, 95.00, 100.00, BAD, 100.00, 1000, 100.00, 50, 500, 50.00
"""
        result = _parse_bhav_csv(csv_text)
        assert "BADSTOCK" not in result

    def test_missing_delivery_pct_defaults_zero(self):
        csv_text = """\
SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
NODELIV, EQ, 25-APR-2026, 99.00, 100.00, 105.00, 95.00, 101.00, 102.00, 101.00, 1000, 100.00, 50, 0,
"""
        result = _parse_bhav_csv(csv_text)
        assert result["NODELIV"]["delivery_pct"] == 0.0


class TestBuildBhavUrl:
    """Test URL construction."""

    def test_url_format(self):
        d = date(2026, 4, 25)
        url = _build_bhav_url(d)
        assert url == (
            "https://nsearchives.nseindia.com/products/content"
            "/sec_bhavdata_full_25042026.csv"
        )

    def test_january(self):
        d = date(2026, 1, 5)
        url = _build_bhav_url(d)
        assert "sec_bhavdata_full_05012026.csv" in url

    def test_december(self):
        d = date(2025, 12, 31)
        url = _build_bhav_url(d)
        assert "sec_bhavdata_full_31122025.csv" in url


class TestPreviousTradingDay:
    """Test previous trading day calculation."""

    def test_weekday_returns_previous_day(self):
        # Tuesday -> Monday
        assert _previous_trading_day(date(2026, 4, 28)) == date(2026, 4, 27)

    def test_monday_returns_friday(self):
        assert _previous_trading_day(date(2026, 4, 27)) == date(2026, 4, 24)

    def test_skips_weekend(self):
        # Sunday -> Friday
        assert _previous_trading_day(date(2026, 4, 26)) == date(2026, 4, 24)

    def test_skips_holiday(self):
        # Day after Republic Day 2026 (Jan 26 is Monday, so Jan 27 Tue -> Jan 23 Fri)
        assert _previous_trading_day(date(2026, 1, 27)) == date(2026, 1, 23)


class TestFetchBhavCopy:
    """Test the orchestrator function with mocked download and Redis."""

    @pytest.mark.asyncio
    async def test_stores_in_redis_with_correct_key(self):
        trade_date = date(2026, 4, 24)
        parsed_data = {
            "RELIANCE": {"delivery_pct": 50.0, "close": 2470.0, "prev_close": 2440.0},
            "TCS": {"delivery_pct": 60.0, "close": 3520.0, "prev_close": 3490.0},
        }

        mock_redis = AsyncMock()

        with (
            patch(
                "app.tasks.nse_bhav_copy_task._download_bhav_copy",
                return_value=parsed_data,
            ),
            patch(
                "app.core.redis.get_redis",
                return_value=mock_redis,
            ),
        ):
            result = await fetch_bhav_copy(trade_date)

        assert result == parsed_data
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        key = call_args[0][0]
        ttl = call_args[0][1]
        stored_json = call_args[0][2]

        assert key == "nse:bhav_copy:2026-04-24"
        assert ttl == 90 * 86400
        stored = json.loads(stored_json)
        assert stored["RELIANCE"]["delivery_pct"] == 50.0
        assert stored["TCS"]["close"] == 3520.0

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        """Should retry up to 3 times with backoff."""
        trade_date = date(2026, 4, 24)

        call_count = 0

        async def _mock_download(d):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return None
            return {"TCS": {"delivery_pct": 60.0, "close": 3520.0, "prev_close": 3490.0}}

        mock_redis = AsyncMock()

        with (
            patch(
                "app.tasks.nse_bhav_copy_task._download_bhav_copy",
                side_effect=_mock_download,
            ),
            patch(
                "app.core.redis.get_redis",
                return_value=mock_redis,
            ),
            patch("app.tasks.nse_bhav_copy_task.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await fetch_bhav_copy(trade_date)

        assert result is not None
        assert call_count == 3
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries_exhausted(self):
        trade_date = date(2026, 4, 24)

        with patch(
            "app.tasks.nse_bhav_copy_task._download_bhav_copy",
            return_value=None,
        ):
            result = await fetch_bhav_copy(trade_date)

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_previous_trading_day_when_no_date(self):
        parsed_data = {"INFY": {"delivery_pct": 30.0, "close": 1410.0, "prev_close": 1395.0}}
        mock_redis = AsyncMock()

        with (
            patch(
                "app.tasks.nse_bhav_copy_task._download_bhav_copy",
                return_value=parsed_data,
            ) as mock_dl,
            patch(
                "app.core.redis.get_redis",
                return_value=mock_redis,
            ),
            patch(
                "app.core.utils.now_ist",
                return_value=MagicMock(date=MagicMock(return_value=date(2026, 4, 28))),
            ),
        ):
            result = await fetch_bhav_copy(None)

        assert result == parsed_data
        # Should have called download with previous trading day (Apr 27 is Mon)
        mock_dl.assert_called_once_with(date(2026, 4, 27))


class TestGetBhavCopy:
    """Test Redis reader."""

    @pytest.mark.asyncio
    async def test_returns_data_from_redis(self):
        stored = {"RELIANCE": {"delivery_pct": 50.0, "close": 2470.0, "prev_close": 2440.0}}
        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(stored)

        with patch("app.core.redis.get_redis", return_value=mock_redis):
            result = await get_bhav_copy(date(2026, 4, 24))

        assert result == stored
        mock_redis.get.assert_called_once_with("nse:bhav_copy:2026-04-24")

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        with patch("app.core.redis.get_redis", return_value=mock_redis):
            result = await get_bhav_copy(date(2026, 4, 24))

        assert result is None
