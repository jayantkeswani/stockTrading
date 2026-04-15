"""Tests for core utility functions."""

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.core.constants import IST
from app.core.utils import (
    is_in_dead_zone,
    is_in_trading_window,
    is_market_open,
    is_past_close_deadline,
    now_ist,
    time_to_market_close_minutes,
)


class TestNowIst:

    def test_returns_ist_timezone(self):
        result = now_ist()
        assert result.tzinfo is not None
        # The tzinfo should resolve to Asia/Kolkata
        assert str(result.tzinfo) == "Asia/Kolkata"

    def test_returns_datetime(self):
        result = now_ist()
        assert isinstance(result, datetime)


class TestIsMarketOpen:

    def _mock_now(self, hour, minute, weekday=0):
        """Create a mock datetime for a given IST time on a given weekday."""
        # weekday 0=Monday, 5=Saturday
        # We pick a date that corresponds to the desired weekday
        # 2026-04-13 is Monday (weekday=0)
        day = 13 + weekday
        return datetime(2026, 4, day, hour, minute, 0, tzinfo=IST)

    @patch("app.core.utils.now_ist")
    def test_market_open_during_hours(self, mock_now):
        mock_now.return_value = self._mock_now(10, 30)
        assert is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_closed_before_open(self, mock_now):
        mock_now.return_value = self._mock_now(9, 0)
        assert not is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_closed_after_close(self, mock_now):
        mock_now.return_value = self._mock_now(15, 31)
        assert not is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_open_at_exact_open(self, mock_now):
        mock_now.return_value = self._mock_now(9, 15)
        assert is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_open_at_exact_close(self, mock_now):
        mock_now.return_value = self._mock_now(15, 30)
        assert is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_closed_on_saturday(self, mock_now):
        mock_now.return_value = self._mock_now(10, 30, weekday=5)
        assert not is_market_open()

    @patch("app.core.utils.now_ist")
    def test_market_closed_on_sunday(self, mock_now):
        mock_now.return_value = self._mock_now(10, 30, weekday=6)
        assert not is_market_open()


class TestIsInTradingWindow:

    @patch("app.core.utils.now_ist")
    def test_in_window_1(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 10, 0, 0, tzinfo=IST)
        assert is_in_trading_window()

    @patch("app.core.utils.now_ist")
    def test_in_window_2(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 14, 0, 0, tzinfo=IST)
        assert is_in_trading_window()

    @patch("app.core.utils.now_ist")
    def test_not_in_any_window(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=IST)
        assert not is_in_trading_window()

    @patch("app.core.utils.now_ist")
    def test_at_window_1_start(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 9, 45, 0, tzinfo=IST)
        assert is_in_trading_window()

    @patch("app.core.utils.now_ist")
    def test_at_window_1_end(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 11, 0, 0, tzinfo=IST)
        assert is_in_trading_window()


class TestIsInDeadZone:

    @patch("app.core.utils.now_ist")
    def test_in_dead_zone(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=IST)
        assert is_in_dead_zone()

    @patch("app.core.utils.now_ist")
    def test_not_in_dead_zone(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 10, 0, 0, tzinfo=IST)
        assert not is_in_dead_zone()

    @patch("app.core.utils.now_ist")
    def test_at_dead_zone_start(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 11, 30, 0, tzinfo=IST)
        assert is_in_dead_zone()

    @patch("app.core.utils.now_ist")
    def test_at_dead_zone_end(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 13, 30, 0, tzinfo=IST)
        assert is_in_dead_zone()


class TestIsPastCloseDeadline:

    @patch("app.core.utils.now_ist")
    def test_past_deadline(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 15, 20, 0, tzinfo=IST)
        assert is_past_close_deadline()

    @patch("app.core.utils.now_ist")
    def test_before_deadline(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 14, 0, 0, tzinfo=IST)
        assert not is_past_close_deadline()

    @patch("app.core.utils.now_ist")
    def test_at_deadline(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 15, 15, 0, tzinfo=IST)
        assert is_past_close_deadline()


class TestTimeToMarketCloseMinutes:

    @patch("app.core.utils.now_ist")
    def test_during_market_hours(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 14, 30, 0, tzinfo=IST)
        minutes = time_to_market_close_minutes()
        assert minutes == 60  # 15:30 - 14:30

    @patch("app.core.utils.now_ist")
    def test_at_market_close(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 15, 30, 0, tzinfo=IST)
        minutes = time_to_market_close_minutes()
        assert minutes == 0

    @patch("app.core.utils.now_ist")
    def test_after_market_close_returns_zero(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 13, 16, 0, 0, tzinfo=IST)
        minutes = time_to_market_close_minutes()
        assert minutes == 0
