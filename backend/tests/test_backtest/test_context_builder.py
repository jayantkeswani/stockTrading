"""Unit tests for backtest/context_builder.py — pure logic, no DB needed."""

from datetime import date, timedelta

import pytest

from app.backtest.context_builder import _aggregate_to_5m
from app.indicators.candle_patterns import Candle


class TestAgggregateTo5m:
    def _candle(self, o, h, l, c, v=100) -> Candle:
        return Candle(open=o, high=h, low=l, close=c, volume=v)

    def test_five_candles_become_one(self):
        candles = [
            self._candle(100, 105, 99, 102, 200),
            self._candle(102, 106, 101, 103, 150),
            self._candle(103, 107, 102, 104, 180),
            self._candle(104, 108, 103, 105, 160),
            self._candle(105, 110, 104, 108, 200),
        ]
        result = _aggregate_to_5m(candles)
        assert len(result) == 1
        bar = result[0]
        assert bar.open == 100
        assert bar.high == 110
        assert bar.low == 99
        assert bar.close == 108
        assert bar.volume == 200 + 150 + 180 + 160 + 200

    def test_ten_candles_become_two(self):
        candles = [
            self._candle(100, 102, 99, 101) for _ in range(10)
        ]
        result = _aggregate_to_5m(candles)
        assert len(result) == 2

    def test_partial_last_group(self):
        # 7 candles → one full 5m bar + one 2-candle partial
        candles = [self._candle(100, 102, 99, 101) for _ in range(7)]
        result = _aggregate_to_5m(candles)
        assert len(result) == 2

    def test_empty_returns_empty(self):
        assert _aggregate_to_5m([]) == []

    def test_four_candles_one_partial_bar(self):
        candles = [self._candle(100, 102, 99, 101) for _ in range(4)]
        result = _aggregate_to_5m(candles)
        assert len(result) == 1


class TestStrikeSelector:
    def test_weekly_expiry_nifty_after_tuesday(self):
        from app.backtest.strike_selector import select_expiry_as_of
        # 2025-10-20 is a Monday — next Tuesday is 2025-10-21
        d = date(2025, 10, 20)
        expiry = select_expiry_as_of("NIFTY", d)
        assert expiry == date(2025, 10, 21)

    def test_weekly_expiry_nifty_on_tuesday(self):
        from app.backtest.strike_selector import select_expiry_as_of
        # On the expiry day itself — return the same day
        d = date(2025, 10, 21)
        expiry = select_expiry_as_of("NIFTY", d)
        assert expiry == date(2025, 10, 21)

    def test_monthly_expiry_banknifty(self):
        from app.backtest.strike_selector import select_expiry_as_of
        # BANKNIFTY is monthly — last Tuesday of the month
        d = date(2025, 10, 1)
        expiry = select_expiry_as_of("BANKNIFTY", d)
        # Last Tuesday of Oct 2025
        assert expiry.month == 10
        assert expiry.weekday() == 1  # Tuesday
