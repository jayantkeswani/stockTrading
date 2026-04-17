"""Tests for futures resolver — expiry calculation and contract resolution."""

from datetime import date

import pytest

from app.services.futures_resolver import _find_nearest_monthly_expiry, _last_dow_of_month


class TestLastDowOfMonth:
    def test_last_thursday_april_2026(self):
        # April 2026: 30th is Thursday
        result = _last_dow_of_month(2026, 4, 3)  # Thursday = 3
        assert result == date(2026, 4, 30)

    def test_last_thursday_may_2026(self):
        # May 2026: 28th is Thursday
        result = _last_dow_of_month(2026, 5, 3)
        assert result == date(2026, 5, 28)

    def test_last_thursday_january_2026(self):
        # Jan 2026: 29th is Thursday
        result = _last_dow_of_month(2026, 1, 3)
        assert result == date(2026, 1, 29)

    def test_last_tuesday(self):
        # April 2026: last Tuesday is 28th
        result = _last_dow_of_month(2026, 4, 1)  # Tuesday = 1
        assert result == date(2026, 4, 28)


class TestFindNearestMonthlyExpiry:
    def test_before_expiry_this_month(self):
        # April 17, 2026 — expiry is April 30, 2026 (last Thursday)
        result = _find_nearest_monthly_expiry(date(2026, 4, 17))
        assert result == date(2026, 4, 30)

    def test_after_expiry_uses_next_month(self):
        # May 29, 2026 — past May's last Thursday (28th), use June
        result = _find_nearest_monthly_expiry(date(2026, 5, 29))
        assert result.month == 6

    def test_on_expiry_day_uses_this_month(self):
        result = _find_nearest_monthly_expiry(date(2026, 4, 30))
        assert result == date(2026, 4, 30)

    def test_december_rollover(self):
        # Dec 31 2026 is Thursday (last Thursday) — on that day, return same day
        result = _find_nearest_monthly_expiry(date(2026, 12, 31))
        assert result == date(2026, 12, 31)

        # After December expiry, should go to January next year
        result = _find_nearest_monthly_expiry(date(2027, 1, 1))
        assert result.year == 2027
        assert result.month == 1
