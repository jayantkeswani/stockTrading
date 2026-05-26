"""Tests for POST /api/v1/trades/hold-analysis endpoint.

Covers S5 (intraday_futures) vs options symbol routing, open/missing/late-exit
skip logic, invalid scenario validation, and no-data fallback.
"""

import uuid
from datetime import datetime, time as dt_time, date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytz
import pytest

_IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_ist(hour: int, minute: int, second: int = 0, trading_date: date | None = None) -> datetime:
    """Return a timezone-aware UTC datetime constructed from IST hour/minute on the given date."""
    if trading_date is None:
        trading_date = date(2026, 5, 22)
    naive = datetime(trading_date.year, trading_date.month, trading_date.day, hour, minute, second)
    return _IST.localize(naive)


def _make_trade(
    *,
    strategy_name: str = "intraday_futures",
    symbol: str = "VEDL",
    fyers_option_symbol: str | None = None,
    status: str = "CLOSED",
    exit_time: datetime | None = None,
    entry_time: datetime | None = None,
    entry_price: Decimal = Decimal("500.00"),
    quantity: int = 800,
    side: str = "BUY",
    option_type: str | None = None,
) -> MagicMock:
    """Create a mock Trade object with fields used by hold_analysis."""
    t = MagicMock()
    t.id = uuid.uuid4()
    t.strategy_name = strategy_name
    t.symbol = symbol
    t.fyers_option_symbol = fyers_option_symbol
    t.status = status
    t.entry_time = entry_time or _ts_ist(9, 30)
    t.exit_time = exit_time
    t.entry_price = entry_price
    t.quantity = quantity
    t.side = side
    t.option_type = option_type
    return t


def _make_db_for_trades(trades: list) -> AsyncMock:
    """Build a mock AsyncSession whose execute().scalars().all() returns *trades*.

    The second execute() call (the aggregation query) returns row data driven
    by the *agg_row* attribute on the trade mock, if present.
    """
    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count == 1:
            # First call: fetch trades by IDs
            mock_result.scalars.return_value.all.return_value = trades
        else:
            # Subsequent calls: aggregation query per trade
            # Pull agg_row from the currently iterated trade (set externally)
            trade = _current_trade[0]
            agg_row = getattr(trade, "_agg_row", (None, None))
            mock_result.one_or_none.return_value = agg_row
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=_execute)
    return mock_db


# Mutable container used to tell _make_db_for_trades which trade's agg data to serve
_current_trade: list = [None]


def _make_db_with_agg(
    trade: MagicMock,
    agg_row: tuple,
    exit_time_val: datetime | None = None,
    reentry_entries: list[datetime] | None = None,
    outcome_candles: list[tuple] | None = None,
    eod_fallback: tuple | None = None,
    scenario: str = "best",
) -> AsyncMock:
    """Convenience wrapper: single trade with one aggregation result.

    Call sequence varies by scenario:
    - best/worst: trades fetch, re-entry, agg, timestamp (4 calls)
    - sl_tgt: trades fetch, re-entry, agg, candle walk, [EOD fallback] (4-5 calls)
    """
    trade._agg_row = agg_row
    _current_trade[0] = trade

    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count == 1:
            mock_result.scalars.return_value.all.return_value = [trade]
        elif call_count == 2:
            entries = reentry_entries or []
            mock_result.all.return_value = [(et,) for et in entries]
        elif call_count == 3:
            _current_trade[0] = trade
            mock_result.one_or_none.return_value = agg_row
        elif call_count == 4:
            if scenario == "sl_tgt":
                mock_result.all.return_value = outcome_candles or []
            else:
                mock_result.scalar_one_or_none.return_value = exit_time_val or _ts_ist(14, 30)
        elif call_count == 5:
            mock_result.one_or_none.return_value = eod_fallback
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=_execute)
    return mock_db


def _make_db_no_data(trade: MagicMock) -> AsyncMock:
    """DB returns the trade but the aggregation query returns (None, None)."""
    return _make_db_with_agg(trade, (None, None))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHoldAnalysisS5BestCase:
    """S5 trade, scenario=best → hold_exit_price reflects the seeded max."""

    @pytest.mark.asyncio
    async def test_hold_analysis_s5_best_case(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="CLOSED",
            exit_time=_ts_ist(11, 0),
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
        )
        db = _make_db_with_agg(trade, (Decimal("520.50"), Decimal("490.00")))

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        assert len(response.results) == 1
        r = response.results[0]
        assert r.trade_id == trade.id
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("520.50")
        assert r.hold_pnl == Decimal("20.50") * 800
        assert r.hold_net_pnl is not None
        assert r.hold_net_pnl < r.hold_pnl
        assert r.hold_charges_json is not None
        assert r.hold_exit_time is not None


class TestHoldAnalysisS5WorstCase:
    """S5 trade, scenario=worst → hold_exit_price reflects the seeded minimum."""

    @pytest.mark.asyncio
    async def test_hold_analysis_s5_worst_case(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="SAIL",
            status="CLOSED",
            exit_time=_ts_ist(10, 30),
            entry_price=Decimal("200.00"),
            quantity=800,
            side="BUY",
        )
        db = _make_db_with_agg(trade, (Decimal("210.00"), Decimal("195.75")))

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="worst")
        response = await hold_analysis(body, db)

        assert len(response.results) == 1
        r = response.results[0]
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("195.75")
        assert r.hold_pnl == Decimal("-4.25") * 800
        assert r.hold_net_pnl is not None
        assert r.hold_charges_json is not None


class TestHoldAnalysisS2OptionSymbolUsed:
    """S2 (vwap_pullback) trade uses fyers_option_symbol as the market_data_1m key."""

    @pytest.mark.asyncio
    async def test_hold_analysis_s2_option_symbol_used(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        fyers_sym = "NSE:NIFTY2552222500CE"
        trade = _make_trade(
            strategy_name="vwap_pullback",
            symbol="NIFTY",
            fyers_option_symbol=fyers_sym,
            status="CLOSED",
            exit_time=_ts_ist(12, 15),
            entry_price=Decimal("300.00"),
            quantity=375,
            side="BUY",
            option_type="CE",
        )
        db = _make_db_with_agg(trade, (Decimal("330.00"), Decimal("280.00")))

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("330.00")
        assert r.hold_pnl == Decimal("30.00") * 375
        assert r.hold_charges_json is not None

        # 4 execute calls: trades fetch + re-entry query + agg + timestamp
        assert db.execute.call_count == 4


class TestHoldAnalysisOpenTradeSkipped:
    """OPEN status trade → data_found=False, hold_exit_price=None."""

    @pytest.mark.asyncio
    async def test_hold_analysis_open_trade_skipped(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="OPEN",
            exit_time=None,
        )
        # DB only needs to return the trade; no aggregation call should happen
        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = [trade]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result_trades)

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, mock_db)

        assert len(response.results) == 1
        r = response.results[0]
        assert r.data_found is False
        assert r.hold_exit_price is None
        # Aggregation should never be queried for an open trade
        assert mock_db.execute.call_count == 1


class TestHoldAnalysisNoMarketData:
    """Closed trade but no 1m rows exist in the window → data_found=False."""

    @pytest.mark.asyncio
    async def test_hold_analysis_no_market_data(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="SUNPHARMA",
            status="CLOSED",
            exit_time=_ts_ist(14, 0),
        )
        db = _make_db_no_data(trade)

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is False
        assert r.hold_exit_price is None


class TestHoldAnalysisExitAfterMarketClose:
    """exit_time after 15:30 IST — window is entry_time→15:30, still valid."""

    @pytest.mark.asyncio
    async def test_hold_analysis_exit_after_market_close(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="CLOSED",
            exit_time=_ts_ist(15, 35),  # 5 minutes past market close
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
        )
        # Window is entry_time (9:30) → 15:30, queries proceed
        db = _make_db_with_agg(trade, (Decimal("520.00"), Decimal("490.00")))

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="worst")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("490.00")
        assert r.hold_pnl == Decimal("-10.00") * 800


class TestHoldAnalysisInvalidScenario:
    """scenario not in ('best', 'worst') → HTTP 400."""

    @pytest.mark.asyncio
    async def test_hold_analysis_invalid_scenario(self):
        from fastapi import HTTPException
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        mock_db = AsyncMock()
        body = HoldAnalysisRequest(trade_ids=[uuid.uuid4()], scenario="foo")

        with pytest.raises(HTTPException) as exc_info:
            await hold_analysis(body, mock_db)

        assert exc_info.value.status_code == 400


class TestHoldAnalysisMissingFyersSymbol:
    """S2 trade with fyers_option_symbol=None → falls back to trade.symbol for lookup."""

    @pytest.mark.asyncio
    async def test_hold_analysis_missing_fyers_symbol(self):
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="vwap_pullback",
            symbol="BANKNIFTY",
            fyers_option_symbol=None,
            status="CLOSED",
            exit_time=_ts_ist(11, 45),
        )
        # Falls back to trade.symbol, queries proceed but no 1m data found
        db = _make_db_no_data(trade)

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is False
        assert r.hold_exit_price is None


class TestHoldAnalysisReentryCapping:
    """Hold window capped at next same-instrument same-side re-entry."""

    @pytest.mark.asyncio
    async def test_reentry_caps_window(self):
        """Trade 1 exits at 10:14, trade 2 re-enters at 10:15 on same symbol+side.
        Hold window for trade 1 should be capped at 10:15, not 15:30."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="CLOSED",
            exit_time=_ts_ist(10, 14),
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
        )
        # Re-entry at 10:15 — the reentry query returns this entry_time
        reentry_at = _ts_ist(10, 15)
        # max_high=510 in the CAPPED window (10:14 to 10:15)
        db = _make_db_with_agg(
            trade,
            (Decimal("510.00"), Decimal("495.00")),
            reentry_entries=[reentry_at],
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("510.00")
        # P&L from entry 500 to hold exit 510, qty 800
        assert r.hold_pnl == Decimal("10.00") * 800

    @pytest.mark.asyncio
    async def test_opposite_side_does_not_cap(self):
        """BUY exit followed by SELL entry on same symbol should NOT cap."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="CLOSED",
            exit_time=_ts_ist(10, 14),
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
        )
        # Re-entry query returns empty — opposite side trade not matched
        db = _make_db_with_agg(
            trade,
            (Decimal("520.00"), Decimal("490.00")),
            reentry_entries=[],
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is True
        # Full window to 15:30 — uncapped
        assert r.hold_exit_price == Decimal("520.00")
        assert r.hold_pnl == Decimal("20.00") * 800

    @pytest.mark.asyncio
    async def test_last_trade_uses_market_close(self):
        """Last trade in a sequence (no subsequent re-entry) uses 15:30 cutoff."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            strategy_name="intraday_futures",
            symbol="VEDL",
            status="CLOSED",
            exit_time=_ts_ist(14, 0),
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
        )
        # No re-entries after this trade
        db = _make_db_with_agg(
            trade,
            (Decimal("515.00"), Decimal("498.00")),
            reentry_entries=[],
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="best")
        response = await hold_analysis(body, db)

        r = response.results[0]
        assert r.data_found is True
        assert r.hold_exit_price == Decimal("515.00")
        assert r.hold_pnl == Decimal("15.00") * 800


class TestHoldAnalysisOutcome:
    """hold_outcome: SL, TGT, or None based on candle data in hold window."""

    @pytest.mark.asyncio
    async def test_target_hit(self):
        """Candle high reaches target → outcome=TGT."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
            status="CLOSED",
            exit_time=_ts_ist(11, 0),
        )
        trade.stop_loss = Decimal("485.00")
        trade.target_price = Decimal("522.00")
        candles = [
            (Decimal("515.00"), Decimal("498.00"), _ts_ist(11, 1)),  # no hit
            (Decimal("525.00"), Decimal("510.00"), _ts_ist(11, 2)),  # high=525 >= 522 — TGT hit
        ]
        db = _make_db_with_agg(
            trade,
            (Decimal("525.00"), Decimal("498.00")),
            outcome_candles=candles,
            scenario="sl_tgt",
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="sl_tgt")
        response = await hold_analysis(body, db)
        r = response.results[0]
        assert r.hold_outcome == "TGT"
        assert r.hold_exit_price == Decimal("522.00")

    @pytest.mark.asyncio
    async def test_sl_hit(self):
        """Candle low breaches SL → outcome=SL."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
            status="CLOSED",
            exit_time=_ts_ist(11, 0),
        )
        trade.stop_loss = Decimal("485.00")
        trade.target_price = Decimal("522.00")
        candles = [
            (Decimal("505.00"), Decimal("495.00"), _ts_ist(11, 1)),  # no hit
            (Decimal("498.00"), Decimal("480.00"), _ts_ist(11, 2)),  # low=480 <= 485 — SL hit
        ]
        db = _make_db_with_agg(
            trade,
            (Decimal("505.00"), Decimal("480.00")),
            outcome_candles=candles,
            scenario="sl_tgt",
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="sl_tgt")
        response = await hold_analysis(body, db)
        r = response.results[0]
        assert r.hold_outcome == "SL"
        assert r.hold_exit_price == Decimal("485.00")

    @pytest.mark.asyncio
    async def test_neither_hit(self):
        """No candle reaches SL or target → outcome=None."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
            status="CLOSED",
            exit_time=_ts_ist(11, 0),
        )
        trade.stop_loss = Decimal("485.00")
        trade.target_price = Decimal("522.00")
        candles = [
            (Decimal("510.00"), Decimal("495.00"), _ts_ist(11, 1)),  # no hit
            (Decimal("515.00"), Decimal("490.00"), _ts_ist(11, 2)),  # no hit
        ]
        db = _make_db_with_agg(
            trade,
            (Decimal("515.00"), Decimal("490.00")),
            outcome_candles=candles,
            eod_fallback=(Decimal("512.00"), _ts_ist(15, 29)),
            scenario="sl_tgt",
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="sl_tgt")
        response = await hold_analysis(body, db)
        r = response.results[0]
        assert r.hold_outcome is None
        assert r.hold_exit_price == Decimal("512.00")

    @pytest.mark.asyncio
    async def test_sl_hit_first_when_both_breached(self):
        """SL hit on candle 1, target hit on candle 2 → outcome=SL (first wins)."""
        from app.api.v1.trades import hold_analysis
        from app.schemas.trade import HoldAnalysisRequest

        trade = _make_trade(
            entry_price=Decimal("500.00"),
            quantity=800,
            side="BUY",
            status="CLOSED",
            exit_time=_ts_ist(11, 0),
        )
        trade.stop_loss = Decimal("485.00")
        trade.target_price = Decimal("522.00")
        candles = [
            (Decimal("505.00"), Decimal("480.00"), _ts_ist(11, 1)),  # SL hit first (low=480 <= 485)
            (Decimal("525.00"), Decimal("510.00"), _ts_ist(11, 2)),  # TGT hit later
        ]
        db = _make_db_with_agg(
            trade,
            (Decimal("525.00"), Decimal("480.00")),
            outcome_candles=candles,
            scenario="sl_tgt",
        )

        body = HoldAnalysisRequest(trade_ids=[trade.id], scenario="sl_tgt")
        response = await hold_analysis(body, db)
        r = response.results[0]
        assert r.hold_outcome == "SL"
        assert r.hold_exit_price == Decimal("485.00")
