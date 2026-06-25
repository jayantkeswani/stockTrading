"""DB + calendar helpers for the Intraday Hunter agent (backend async-session path).

The offline prototype (`scripts/intraday_hunter/prototype_agent.py`) reads candles via
raw asyncpg; the live backend reads the same rows through the app's async SQLAlchemy
session. These helpers keep the queries byte-for-byte equivalent (IST timezone conversion,
the 09:15-15:30 in-session filter, the positivity filter) so the live agent sees exactly
what the validated prototype saw.

Candle format returned everywhere: {ts: IST ISO str, open, high, low, close, volume} —
the shape `context.py` and `charts.py` expect.
"""
from __future__ import annotations

import os
from datetime import date, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VIX_SYMBOL = "INDIA VIX"
MARKET_OPEN = time(9, 15)

# Where rendered chart PNGs live. Same-day persistent; served by the API by stored path.
CHART_DIR = os.environ.get("INTRADAY_HUNTER_CHART_DIR", "/tmp/intraday_hunter_charts")


_DAY_SQL = text(
    """
    SELECT (timestamp AT TIME ZONE 'Asia/Kolkata') AS ts, open, high, low, close, volume
    FROM market_data_1m
    WHERE symbol = :symbol
      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::time BETWEEN '09:15' AND '15:30'
    ORDER BY timestamp
    """
)

_PREV_DATE_SQL = text(
    """
    SELECT MAX((timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS pd
    FROM market_data_1m
    WHERE symbol = :symbol
      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date < :d
    """
)

_VIX_SQL = text(
    """
    SELECT close FROM market_data_1m
    WHERE symbol = :symbol
      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::time <= :cutoff
    ORDER BY timestamp DESC LIMIT 1
    """
)


async def fetch_day(session: AsyncSession, symbol: str, d: date) -> list[dict]:
    """In-session 1m candles for `symbol` on date `d` (ts as IST ISO str). Drops zero rows."""
    rows = (await session.execute(_DAY_SQL, {"symbol": symbol, "d": d})).all()
    out: list[dict] = []
    for r in rows:
        if min(r.open, r.high, r.low, r.close) <= 0:
            continue
        out.append(
            {
                "ts": r.ts.isoformat(),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
        )
    return out


async def prev_trading_date(session: AsyncSession, symbol: str, d: date) -> date | None:
    """Most recent date strictly before `d` that has 1m candles for `symbol`."""
    row = (await session.execute(_PREV_DATE_SQL, {"symbol": symbol, "d": d})).first()
    return row.pd if row else None


async def fetch_vix(
    session: AsyncSession, d: date, upto: time | None = None
) -> float | None:
    """Most recent INDIA VIX close on date `d` up to `upto` (defaults to EOD)."""
    cutoff = upto or time(15, 30)
    row = (
        await session.execute(
            _VIX_SQL, {"symbol": VIX_SYMBOL, "d": d, "cutoff": cutoff}
        )
    ).first()
    return float(row.close) if row and row.close else None


def compute_calendar(d: date) -> dict:
    """Expiry calendar for date `d`: NIFTY weekly = Tuesday, SENSEX weekly = Thursday.

    Weekday-based (matches the validated prototype). Other indices are monthly-only
    post-SEBI Nov 2024, so they never flag a weekly expiry here.
    """
    wd = d.weekday()
    if wd == 1:  # Tuesday
        return {"is_expiry": True, "expiry_index": "NIFTY", "holiday_ahead": False}
    if wd == 3:  # Thursday
        return {"is_expiry": True, "expiry_index": "SENSEX", "holiday_ahead": False}
    return {"is_expiry": False, "expiry_index": None, "holiday_ahead": False}
