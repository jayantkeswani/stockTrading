"""Persistence helpers for the Intraday Hunter agent — the `intraday_hunter_runs` row.

One row per trading day. Call 1 fills the thesis; each Call 2 updates the latest decision
and appends to the audit history. Also builds the multi-day STRUCTURAL memory snapshot fed
into Call 1 (trapped side / direction / whether the thesis played out — never rupee P&L).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intraday_hunter_run import IntradayHunterRun


async def get_run(session: AsyncSession, trading_date: date) -> IntradayHunterRun | None:
    """The run row for `trading_date`, or None."""
    res = await session.execute(
        select(IntradayHunterRun).where(IntradayHunterRun.trading_date == trading_date)
    )
    return res.scalar_one_or_none()


async def get_or_create_run(
    session: AsyncSession, trading_date: date
) -> IntradayHunterRun:
    """Fetch the run for `trading_date`, creating a PENDING row if absent (idempotent)."""
    run = await get_run(session, trading_date)
    if run is None:
        run = IntradayHunterRun(trading_date=trading_date, status="PENDING")
        session.add(run)
        await session.flush()
    return run


async def recent_runs(
    session: AsyncSession, before_date: date, limit: int = 3
) -> list[IntradayHunterRun]:
    """The `limit` most recent run rows strictly before `before_date` (newest first)."""
    res = await session.execute(
        select(IntradayHunterRun)
        .where(IntradayHunterRun.trading_date < before_date)
        .order_by(IntradayHunterRun.trading_date.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def history(
    session: AsyncSession, limit: int = 30
) -> list[IntradayHunterRun]:
    """The `limit` most recent run rows (newest first) for the UI history list."""
    res = await session.execute(
        select(IntradayHunterRun)
        .order_by(IntradayHunterRun.trading_date.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


def _trapped_side(run: IntradayHunterRun) -> str | None:
    """Trapped side for the memory snapshot — the day's decision, else the thesis."""
    if run.call2_json and run.call2_json.get("trapped_side"):
        return run.call2_json.get("trapped_side")
    if run.call1_json and run.call1_json.get("trapped_side"):
        return run.call1_json.get("trapped_side")
    return None


def build_memory_snapshot(runs: list[IntradayHunterRun]) -> list[dict]:
    """Compact STRUCTURAL memory for Call 1 (no rupee P&L).

    [{date, trapped_side, direction, thesis_played_out}], oldest-first so the model reads
    them chronologically. Powers the "has the trapped side already fled / Nth reversal in a
    row" reasoning.
    """
    snap: list[dict] = []
    for run in reversed(runs):  # recent_runs is newest-first; feed oldest-first
        snap.append(
            {
                "date": run.trading_date.isoformat(),
                "trapped_side": _trapped_side(run),
                "direction": run.direction,
                "thesis_played_out": run.outcome_played_out,
            }
        )
    return snap
