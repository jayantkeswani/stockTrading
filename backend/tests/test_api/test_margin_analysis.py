"""Tests for POST /api/v1/trades/margin-analysis endpoint."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_trade(
    entry_time: datetime,
    exit_time: datetime | None,
    margin_required: float | None,
) -> MagicMock:
    """Create a mock Trade object with the fields read by margin_analysis."""
    t = MagicMock()
    t.entry_time = entry_time
    t.exit_time = exit_time
    t.margin_required = Decimal(str(margin_required)) if margin_required is not None else None
    return t


def _make_db(trades: list) -> AsyncMock:
    """Return a mock AsyncSession whose execute().scalars().all() returns *trades*."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = trades

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


# Reusable fixed datetimes (UTC-aware so they sort unambiguously)
T0 = datetime(2026, 5, 22, 9, 15, 0, tzinfo=timezone.utc)   # 9:15 AM
T1 = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)   # 10:00 AM
T2 = datetime(2026, 5, 22, 11, 0, 0, tzinfo=timezone.utc)   # 11:00 AM
T3 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)   # 12:00 PM


class TestMarginAnalysisNoOverlap:
    """Trade B starts after Trade A has already exited — no concurrent exposure."""

    @pytest.mark.asyncio
    async def test_peak_equals_max_individual_margin(self):
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T1, margin_required=50_000)
        trade_b = _make_trade(entry_time=T1, exit_time=T2, margin_required=80_000)

        # Both trade IDs present so trade_count = 2
        id_a = uuid.uuid4()
        id_b = uuid.uuid4()
        body = MarginAnalysisRequest(trade_ids=[id_a, id_b])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.trade_count == 2
        assert response.total_margin == Decimal("130000")
        # Peak = 80_000 (trade_b), because at T1 trade_a exits and trade_b enters
        # simultaneously — exit event (-50k) and entry event (+80k) are at the same
        # timestamp; after sorting, running reaches 80k max.
        assert response.peak_margin == Decimal("80000")
        assert response.peak_time is not None

    @pytest.mark.asyncio
    async def test_peak_equals_larger_trade_when_clearly_sequential(self):
        """Trade A fully closes before Trade B opens — no concurrent margin."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T1, margin_required=30_000)
        trade_b = _make_trade(entry_time=T2, exit_time=T3, margin_required=70_000)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.peak_margin == Decimal("70000")
        assert response.total_margin == Decimal("100000")


class TestMarginAnalysisWithOverlap:
    """Trade B opens before Trade A exits — peak = sum of both margins."""

    @pytest.mark.asyncio
    async def test_peak_equals_sum_of_both_margins(self):
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        # A: 9:15 – 11:00, B: 10:00 – 12:00 → overlap window 10:00–11:00
        trade_a = _make_trade(entry_time=T0, exit_time=T2, margin_required=50_000)
        trade_b = _make_trade(entry_time=T1, exit_time=T3, margin_required=60_000)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.trade_count == 2
        assert response.total_margin == Decimal("110000")
        # Peak = 50k (A entry at T0) + 60k (B entry at T1) = 110k while both open
        assert response.peak_margin == Decimal("110000")
        assert response.peak_time == T1  # peak reached when B enters

    @pytest.mark.asyncio
    async def test_peak_time_is_when_second_trade_enters(self):
        """peak_time must identify the exact moment concurrent margin is highest."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T3, margin_required=40_000)
        trade_b = _make_trade(entry_time=T1, exit_time=T2, margin_required=90_000)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.peak_margin == Decimal("130000")
        assert response.peak_time == T1

    @pytest.mark.asyncio
    async def test_three_trades_partially_overlapping(self):
        """Three trades where peak is reached when all three are open simultaneously."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        # All three open by T1; trade_a closes at T2, trade_b and trade_c at T3
        trade_a = _make_trade(entry_time=T0, exit_time=T2, margin_required=20_000)
        trade_b = _make_trade(entry_time=T0, exit_time=T3, margin_required=30_000)
        trade_c = _make_trade(entry_time=T1, exit_time=T3, margin_required=25_000)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b, trade_c])

        response = await margin_analysis(body, db)

        assert response.trade_count == 3
        # All three open from T1 onward → peak = 20k+30k+25k = 75k
        assert response.peak_margin == Decimal("75000")
        assert response.peak_time == T1


class TestMarginAnalysisEmpty:
    """No trade_ids supplied — endpoint should return zero values."""

    @pytest.mark.asyncio
    async def test_empty_trade_ids_returns_zeros(self):
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        body = MarginAnalysisRequest(trade_ids=[])
        db = _make_db([])

        response = await margin_analysis(body, db)

        assert response.trade_count == 0
        assert response.total_margin == Decimal("0")
        assert response.peak_margin == Decimal("0")
        assert response.peak_time is None

    @pytest.mark.asyncio
    async def test_nonexistent_trade_ids_returns_zeros(self):
        """DB returns no rows for the provided IDs — same zero result."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([])  # DB finds nothing matching those IDs

        response = await margin_analysis(body, db)

        assert response.trade_count == 0
        assert response.total_margin == Decimal("0")
        assert response.peak_margin == Decimal("0")
        assert response.peak_time is None


class TestMarginAnalysisNoMargin:
    """Trades that have margin_required = None or 0 — everything should stay zero."""

    @pytest.mark.asyncio
    async def test_all_trades_missing_margin_required(self):
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T2, margin_required=None)
        trade_b = _make_trade(entry_time=T1, exit_time=T3, margin_required=None)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.trade_count == 2
        assert response.total_margin == Decimal("0")
        assert response.peak_margin == Decimal("0")
        assert response.peak_time is None

    @pytest.mark.asyncio
    async def test_zero_margin_treated_same_as_missing(self):
        """margin_required=0 should not contribute to peak or total."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T2, margin_required=0)
        trade_b = _make_trade(entry_time=T1, exit_time=T3, margin_required=0)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.trade_count == 2
        assert response.total_margin == Decimal("0")
        assert response.peak_margin == Decimal("0")
        assert response.peak_time is None

    @pytest.mark.asyncio
    async def test_mixed_margin_and_no_margin(self):
        """One trade with margin, one without — peak reflects only the margined trade."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_a = _make_trade(entry_time=T0, exit_time=T2, margin_required=45_000)
        trade_b = _make_trade(entry_time=T1, exit_time=T3, margin_required=None)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4(), uuid.uuid4()])
        db = _make_db([trade_a, trade_b])

        response = await margin_analysis(body, db)

        assert response.trade_count == 2
        assert response.total_margin == Decimal("45000")
        assert response.peak_margin == Decimal("45000")
        assert response.peak_time == T0  # peak when trade_a entered

    @pytest.mark.asyncio
    async def test_open_trade_no_exit_time_still_counted(self):
        """An open trade (exit_time=None) contributes to total but has no exit event."""
        from app.api.v1.trades import margin_analysis
        from app.schemas.trade import MarginAnalysisRequest

        trade_open = _make_trade(entry_time=T0, exit_time=None, margin_required=55_000)

        body = MarginAnalysisRequest(trade_ids=[uuid.uuid4()])
        db = _make_db([trade_open])

        response = await margin_analysis(body, db)

        assert response.trade_count == 1
        assert response.total_margin == Decimal("55000")
        assert response.peak_margin == Decimal("55000")
        assert response.peak_time == T0
