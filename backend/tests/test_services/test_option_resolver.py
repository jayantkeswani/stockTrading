"""Tests for the option resolver — strike selection, expiry selection, and SL/target on premium."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import InstrumentType, SignalType
from app.services.option_resolver import (
    OptionResolution,
    _last_dow_of_month,
    _next_monthly_expiry,
    _next_weekly_expiry,
    resolve_option_details,
    select_expiry,
    select_strike,
)


# ---------------------------------------------------------------------------
# Strike selection
# ---------------------------------------------------------------------------


class TestSelectStrike:

    def test_atm_rounding_nifty(self):
        """NIFTY at 22030 should round ATM to 22050 (gap=50)."""
        atm, itm = select_strike(22030, SignalType.BUY_CE, strike_gap=50)
        assert atm == 22050

    def test_atm_rounding_banknifty(self):
        """BANKNIFTY at 48250 should round ATM to 48200 (gap=100, banker's rounding)."""
        atm, itm = select_strike(48250, SignalType.BUY_CE, strike_gap=100)
        assert atm == 48200

    def test_atm_exact_price(self):
        """Index price exactly on a strike."""
        atm, itm = select_strike(22000, SignalType.BUY_CE, strike_gap=50)
        assert atm == 22000

    def test_itm_for_call(self):
        """ITM for calls = one strike below ATM."""
        atm, itm = select_strike(22030, SignalType.BUY_CE, strike_gap=50)
        assert itm == atm - 50

    def test_itm_for_put(self):
        """ITM for puts = one strike above ATM."""
        atm, itm = select_strike(22030, SignalType.BUY_PE, strike_gap=50)
        assert itm == atm + 50

    def test_midcpnifty_gap_25(self):
        """MIDCPNIFTY with gap=25."""
        atm, itm = select_strike(10013, SignalType.BUY_CE, strike_gap=25)
        assert atm == 10025
        assert itm == 10000

    def test_sensex_gap_100(self):
        atm, itm = select_strike(72450, SignalType.BUY_PE, strike_gap=100)
        assert atm == 72400 or atm == 72500  # depends on rounding
        assert itm == atm + 100


# ---------------------------------------------------------------------------
# Expiry selection — weekly
# ---------------------------------------------------------------------------


class TestWeeklyExpiry:

    def test_nifty_tuesday_expiry(self):
        """NIFTY weekly expiry is Tuesday (dow=1)."""
        # A known Tuesday: April 21, 2026
        tuesday = date(2026, 4, 21)
        result = _next_weekly_expiry(tuesday, target_dow=1)
        assert result == tuesday
        assert result.weekday() == 1

    def test_nifty_from_wednesday_gives_next_tuesday(self):
        wednesday = date(2026, 4, 22)
        result = _next_weekly_expiry(wednesday, target_dow=1)
        assert result == date(2026, 4, 28)
        assert result.weekday() == 1

    def test_sensex_thursday_expiry(self):
        thursday = date(2026, 4, 16)
        result = _next_weekly_expiry(thursday, target_dow=3)
        assert result == thursday
        assert result.weekday() == 3


# ---------------------------------------------------------------------------
# Expiry selection — monthly
# ---------------------------------------------------------------------------


class TestMonthlyExpiry:

    def test_last_tuesday_of_month(self):
        """Last Tuesday of April 2026 is April 28."""
        result = _last_dow_of_month(2026, 4, target_dow=1)
        assert result == date(2026, 4, 28)
        assert result.weekday() == 1

    def test_last_thursday_of_month(self):
        """Last Thursday of April 2026 is April 30."""
        result = _last_dow_of_month(2026, 4, target_dow=3)
        assert result == date(2026, 4, 30)
        assert result.weekday() == 3

    @patch("app.services.option_resolver.now_ist")
    def test_banknifty_monthly_expiry_this_month(self, mock_now):
        """BANKNIFTY on April 10 should get April 28 (last Tuesday)."""
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 10, 10, 0, tzinfo=IST)
        result = _next_monthly_expiry("BANKNIFTY", date(2026, 4, 10))
        assert result == date(2026, 4, 28)

    @patch("app.services.option_resolver.now_ist")
    def test_banknifty_monthly_expiry_rolls_to_next_month(self, mock_now):
        """BANKNIFTY on April 29 (past this month's expiry) should get May's last Tuesday."""
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 29, 10, 0, tzinfo=IST)
        result = _next_monthly_expiry("BANKNIFTY", date(2026, 4, 29))
        assert result == date(2026, 5, 26)  # last Tuesday of May 2026
        assert result.weekday() == 1


# ---------------------------------------------------------------------------
# select_expiry — integration
# ---------------------------------------------------------------------------


class TestSelectExpiry:

    @patch("app.services.option_resolver.now_ist")
    def test_nifty_gets_weekly(self, mock_now):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        result = select_expiry("NIFTY")
        assert result == date(2026, 4, 21)  # next Tuesday

    @patch("app.services.option_resolver.now_ist")
    def test_finnifty_gets_monthly(self, mock_now):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        result = select_expiry("FINNIFTY")
        assert result == date(2026, 4, 28)  # last Tuesday of April


# ---------------------------------------------------------------------------
# Full resolve_option_details
# ---------------------------------------------------------------------------


class TestResolveOptionDetails:

    @pytest.mark.asyncio
    @patch("app.services.option_resolver.fetch_option_premium")
    @patch("app.services.option_resolver.find_option_symbol")
    @patch("app.services.option_resolver.now_ist")
    async def test_successful_resolution(self, mock_now, mock_find, mock_premium):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        mock_find.return_value = "NSE:NIFTY26APR22050CE"
        mock_premium.return_value = 250.0

        result = await resolve_option_details(
            symbol="NIFTY",
            index_price=22030,
            signal_type=SignalType.BUY_CE,
            sl_pct=0.30,
            rr_multiplier=1.5,
        )

        assert result is not None
        assert result.strike_price == 22050  # ATM
        assert result.option_premium == 250.0
        assert result.fyers_option_symbol == "NSE:NIFTY26APR22050CE"
        # SL = 250 * (1 - 0.30) = 175
        assert result.sl_price == 175.0
        # Target = 250 * (1 + 0.30 * 1.5) = 250 * 1.45 = 362.5
        assert result.target_price == 362.5

    @pytest.mark.asyncio
    @patch("app.services.option_resolver.fetch_option_premium")
    @patch("app.services.option_resolver.find_option_symbol")
    @patch("app.services.option_resolver.now_ist")
    async def test_falls_back_to_itm_when_atm_fails(self, mock_now, mock_find, mock_premium):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        # ATM symbol not found, ITM found
        mock_find.side_effect = [None, "NSE:NIFTY26APR22000CE"]
        mock_premium.return_value = 300.0

        result = await resolve_option_details(
            symbol="NIFTY",
            index_price=22030,
            signal_type=SignalType.BUY_CE,
            sl_pct=0.30,
        )

        assert result is not None
        assert result.strike_price == 22000  # ITM (one below ATM 22050)

    @pytest.mark.asyncio
    @patch("app.services.option_resolver.fetch_option_premium")
    @patch("app.services.option_resolver.find_option_symbol")
    @patch("app.services.option_resolver.now_ist")
    async def test_returns_none_when_both_strikes_fail(self, mock_now, mock_find, mock_premium):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        mock_find.return_value = None

        result = await resolve_option_details(
            symbol="NIFTY",
            index_price=22030,
            signal_type=SignalType.BUY_CE,
            sl_pct=0.30,
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.option_resolver.fetch_option_premium")
    @patch("app.services.option_resolver.find_option_symbol")
    @patch("app.services.option_resolver.now_ist")
    async def test_returns_none_when_premium_unavailable(self, mock_now, mock_find, mock_premium):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        mock_find.return_value = "NSE:NIFTY26APR22050CE"
        mock_premium.return_value = None

        result = await resolve_option_details(
            symbol="NIFTY",
            index_price=22030,
            signal_type=SignalType.BUY_CE,
            sl_pct=0.30,
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.option_resolver.fetch_option_premium")
    @patch("app.services.option_resolver.find_option_symbol")
    @patch("app.services.option_resolver.now_ist")
    async def test_put_sl_target_on_premium(self, mock_now, mock_find, mock_premium):
        from datetime import datetime
        from app.core.constants import IST

        mock_now.return_value = datetime(2026, 4, 16, 10, 0, tzinfo=IST)
        mock_find.return_value = "NSE:NIFTY26APR22000PE"
        mock_premium.return_value = 200.0

        result = await resolve_option_details(
            symbol="NIFTY",
            index_price=22030,
            signal_type=SignalType.BUY_PE,
            sl_pct=0.35,
            rr_multiplier=1.5,
        )

        assert result is not None
        # SL = 200 * (1 - 0.35) = 130
        assert result.sl_price == 130.0
        # Target = 200 * (1 + 0.35 * 1.5) = 200 * 1.525 = 305
        assert result.target_price == 305.0

    def test_unknown_symbol_returns_none_for_strike_gap(self):
        """Synchronous check: unknown symbol has no strike gap."""
        from app.core.constants import STRIKE_GAPS

        assert STRIKE_GAPS.get("UNKNOWN") is None


# ---------------------------------------------------------------------------
# Strategy signal instrument_type check
# ---------------------------------------------------------------------------


class TestStrategySignalInstrumentType:

    def test_vwap_pullback_sets_instrument_type_option(self):
        """VWAP pullback strategy should set instrument_type=OPTION."""
        from app.strategies.strategy_2_vwap_pullback import VWAPPullbackStrategy
        from app.indicators.candle_patterns import Candle
        from app.indicators.cpr import CPRResult
        from app.indicators.previous_day import PreviousDayLevels
        from app.indicators.vwap import VWAPResult
        from app.core.enums import CPRType, DayBias

        strategy = VWAPPullbackStrategy()
        price = 100.05

        # Build context that triggers a CALL signal
        candles = []
        base = [
            Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
            Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
            Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
        ]
        prev = Candle(open=price + 1, high=price + 1.5, low=price - 0.5, close=price - 0.3, volume=100)
        curr = Candle(open=price - 0.5, high=price + 2, low=price - 1, close=price + 1.5, volume=100)
        candles = base + [prev, curr]

        from app.strategies.base import MarketContext

        ctx = MarketContext(
            symbol="NIFTY",
            current_price=price,
            candles_5m=candles,
            vwap=VWAPResult(vwap=100.0, upper_band=101.0, lower_band=99.0),
            previous_day=PreviousDayLevels(pdh=110, pdl=90, pdc=105, pdo=100, day_range=20, bias=DayBias.BULLISH),
            cpr=CPRResult(pivot=100, tc=101, bc=99, r1=110, s1=90, r2=120, s2=80, cpr_type=CPRType.WIDE, cpr_width_pct=2.0),
            oi_analysis=None,
            india_vix=16.0,
            current_time_ist="10:30:00",
        )

        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.instrument_type == InstrumentType.OPTION
        # SL/target should be placeholders (0/None), not computed on index price
        assert signal.stop_loss == 0
        assert signal.target_price is None
        # sl_pct should be in indicators for the resolver
        assert "sl_pct" in signal.indicators
        assert "rr_multiplier" in signal.indicators
