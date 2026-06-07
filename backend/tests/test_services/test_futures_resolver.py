"""Tests for futures resolver — expiry calculation and contract resolution."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.data_feed.symbol_master import SymbolMaster, _parse_csv_row
from app.services.futures_resolver import (
    _find_futures_symbol,
    _find_nearest_monthly_expiry,
    _last_dow_of_month,
    resolve_futures_contract,
)


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
    # NSE stock futures expire on the LAST TUESDAY of the month (changed from
    # Thursday, effective Sep 2025 — STOCK_FUTURES_EXPIRY_DOW=1).
    def test_before_expiry_this_month(self):
        # April 17, 2026 — expiry is April 28, 2026 (last Tuesday)
        result = _find_nearest_monthly_expiry(date(2026, 4, 17))
        assert result == date(2026, 4, 28)

    def test_after_expiry_uses_next_month(self):
        # May 29, 2026 — past May's last Tuesday (26th), use June
        result = _find_nearest_monthly_expiry(date(2026, 5, 29))
        assert result.month == 6

    def test_on_expiry_day_uses_this_month(self):
        # April 28 2026 is the last Tuesday — on that day, return same day
        result = _find_nearest_monthly_expiry(date(2026, 4, 28))
        assert result == date(2026, 4, 28)

    def test_december_rollover(self):
        # Dec 29 2026 is the last Tuesday — on that day, return same day
        result = _find_nearest_monthly_expiry(date(2026, 12, 29))
        assert result == date(2026, 12, 29)

        # After December expiry, should go to January next year
        result = _find_nearest_monthly_expiry(date(2026, 12, 31))
        assert result.year == 2027
        assert result.month == 1


def _fo_row(*, fyers_symbol, short_name, display, code, epoch="1782813600"):
    """Minimal Fyers FO CSV row for a futures contract."""
    row = [""] * 21
    row[0] = "1"
    row[1] = display
    row[2] = str(code)
    row[3] = "500"
    row[8] = epoch
    row[9] = fyers_symbol
    row[13] = short_name
    row[15] = "-1.0"
    row[16] = "XX"
    return row


def _build_master(rows):
    # Derive the exchange from the Fyers symbol prefix, exactly as the real
    # loader does (NSE_FO rows get e="NSE", BSE_FO rows get e="BSE"). The
    # exchange tag drives the NSE-preference in _find_futures_symbol.
    sm = SymbolMaster()
    sm._symbols = [
        p for r in rows
        if (p := _parse_csv_row(r, r[9].split(":", 1)[0], "FO"))
    ]
    sm._build_index()
    return sm


class TestFindFuturesSymbol:
    """Regression: the underlying name must be matched exactly, never via a
    substring of the full Fyers symbol (which let 'BSE' match 'BSE:BANKEX...')."""

    @pytest.mark.asyncio
    async def test_bse_resolves_to_stock_future_not_bankex(self, monkeypatch):
        # All three contracts share the same expiry, so the only thing that can
        # disambiguate is the underlying name. Old code returned BANKEX.
        rows = [
            _fo_row(fyers_symbol="NSE:BSE26JUNFUT", short_name="BSE",
                    display="BSE 30 Jun 26 FUT", code=13),
            _fo_row(fyers_symbol="BSE:BANKEX26JUNFUT", short_name="BANKEX",
                    display="BANKEX 25 Jun 26 FUT", code=11),
            _fo_row(fyers_symbol="BSE:SENSEX26JUNFUT", short_name="SENSEX",
                    display="SENSEX 25 Jun 26 FUT", code=11),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        expiry = datetime.strptime(sm._symbols[0]["x"], "%d %b %Y").date()
        result = await _find_futures_symbol("BSE", expiry)
        assert result == "NSE:BSE26JUNFUT"

    @pytest.mark.asyncio
    async def test_normal_stock_resolves(self, monkeypatch):
        rows = [
            _fo_row(fyers_symbol="NSE:RELIANCE26JUNFUT", short_name="RELIANCE",
                    display="RELIANCE 30 Jun 26 FUT", code=13),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        expiry = datetime.strptime(sm._symbols[0]["x"], "%d %b %Y").date()
        result = await _find_futures_symbol("RELIANCE", expiry)
        assert result == "NSE:RELIANCE26JUNFUT"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, monkeypatch):
        rows = [
            _fo_row(fyers_symbol="BSE:BANKEX26JUNFUT", short_name="BANKEX",
                    display="BANKEX 25 Jun 26 FUT", code=11),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        # 'BSE' is a substring of the BANKEX symbol but not its underlying name.
        result = await _find_futures_symbol("BSE", date(2026, 6, 25))
        assert result is None

    @pytest.mark.asyncio
    async def test_dual_listed_prefers_nse_over_bse(self, monkeypatch):
        # RELIANCE/HDFCBANK list futures on BOTH exchanges with DIFFERENT
        # expiries: NSE = last Tuesday (30 Jun), BSE = last Thursday (25 Jun).
        # The BSE contract is illiquid and never ticks; always pick NSE.
        rows = [
            _fo_row(fyers_symbol="NSE:RELIANCE26JUNFUT", short_name="RELIANCE",
                    display="RELIANCE 30 Jun 26 FUT", code=13, epoch="1782813600"),
            _fo_row(fyers_symbol="BSE:RELIANCE26JUNFUT", short_name="RELIANCE",
                    display="RELIANCE 25 Jun 26 FUT", code=13, epoch="1782381600"),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        # Exact NSE expiry (last Tuesday) → NSE contract.
        assert await _find_futures_symbol("RELIANCE", date(2026, 6, 30)) == "NSE:RELIANCE26JUNFUT"
        # Defense-in-depth: even if the requested expiry lands on the BSE date
        # (the old last-Thursday bug), the NSE preference still wins via the
        # nearest-on/after fallback.
        assert await _find_futures_symbol("RELIANCE", date(2026, 6, 25)) == "NSE:RELIANCE26JUNFUT"

    @pytest.mark.asyncio
    async def test_bse_only_index_future_falls_back_to_bse(self, monkeypatch):
        # SENSEX/BANKEX index futures have no NSE variant — the NSE preference
        # must be a no-op so the BSE contract still resolves.
        rows = [
            _fo_row(fyers_symbol="BSE:SENSEX26JUNFUT", short_name="SENSEX",
                    display="SENSEX 25 Jun 26 FUT", code=11, epoch="1782381600"),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        assert await _find_futures_symbol("SENSEX", date(2026, 6, 25)) == "BSE:SENSEX26JUNFUT"

    @pytest.mark.asyncio
    async def test_nearest_expiry_fallback_same_underlying(self, monkeypatch):
        # Requested expiry has no exact contract; nearest on/after wins, but
        # only within the same underlying.
        rows = [
            _fo_row(fyers_symbol="NSE:RELIANCE26JULFUT", short_name="RELIANCE",
                    display="RELIANCE 28 Jul 26 FUT", code=13, epoch="1785405600"),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)

        july_expiry = datetime.strptime(sm._symbols[0]["x"], "%d %b %Y").date()
        requested = date(july_expiry.year, july_expiry.month, 1)  # before the contract
        result = await _find_futures_symbol("RELIANCE", requested)
        assert result == "NSE:RELIANCE26JULFUT"


class TestResolveFuturesContractRoll:
    """The expiry-roll path (trade_monitor._roll_futures_position) calls
    resolve_futures_contract(from_date=current_expiry + 1 day). It must roll to
    the NEXT month's NSE contract — never the dual-listed illiquid BSE one."""

    @pytest.mark.asyncio
    async def test_roll_to_next_month_prefers_nse(self, monkeypatch):
        rows = [
            _fo_row(fyers_symbol="NSE:RELIANCE26JUNFUT", short_name="RELIANCE",
                    display="RELIANCE 30 Jun 26 FUT", code=13, epoch="1782813600"),
            _fo_row(fyers_symbol="BSE:RELIANCE26JUNFUT", short_name="RELIANCE",
                    display="RELIANCE 25 Jun 26 FUT", code=13, epoch="1782381600"),
            _fo_row(fyers_symbol="NSE:RELIANCE26JULFUT", short_name="RELIANCE",
                    display="RELIANCE 28 Jul 26 FUT", code=13, epoch="1785232800"),
            _fo_row(fyers_symbol="BSE:RELIANCE26JULFUT", short_name="RELIANCE",
                    display="RELIANCE 30 Jul 26 FUT", code=13, epoch="1785405600"),
        ]
        sm = _build_master(rows)
        monkeypatch.setattr("app.data_feed.symbol_master.symbol_master", sm)
        # Stub the LTP fetch so the test stays off Redis/Fyers — the roll's
        # CONTRACT choice (exchange + next-month expiry) is what we assert.
        monkeypatch.setattr(
            "app.services.futures_resolver._fetch_futures_ltp",
            AsyncMock(return_value=1300.0),
        )

        current_expiry = date(2026, 6, 30)  # NSE June contract (last Tuesday)
        res = await resolve_futures_contract(
            "RELIANCE", entry_price=1300.0,
            from_date=current_expiry + timedelta(days=1),
        )
        assert res is not None
        assert res.fyers_symbol == "NSE:RELIANCE26JULFUT"
        assert res.expiry_date == date(2026, 7, 28)  # July's last Tuesday
