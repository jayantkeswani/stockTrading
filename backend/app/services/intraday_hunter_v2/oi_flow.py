"""Opening option-chain OI flow — the side the option writers are leaning at the open.

Definition (reproduces the research `flow` column, ~/Downloads/ih_research/live_review_2026q3):
per index, nearest expiry, summed over strikes:

    flow_idx = ((PE_OI[t1] - PE_OI[t0]) - (CE_OI[t1] - CE_OI[t0])) / (CE_OI[t0] + PE_OI[t0])

with t0 = 09:16 and t1 = 09:19, and the day's flow = the mean over the indices that have both
snapshots. Positive = puts written faster than calls (support building) → CE; negative → PE.
Before 09:19 the minute log uses the flow-so-far (t1 = the latest snapshot minute).

The 1-minute 09:15-09:45 snapshots come from `oi_snapshot_task.fetch_oi_snapshots_hf`.
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OI_T0 = time(9, 16)
OI_T1 = time(9, 19)


def index_flow(t0: dict, t1: dict) -> float | None:
    """One index's normalized flow from {CE: oi, PE: oi} sums at t0 and t1."""
    try:
        base = float(t0["CE"]) + float(t0["PE"])
        if base <= 0:
            return None
        d_pe = float(t1["PE"]) - float(t0["PE"])
        d_ce = float(t1["CE"]) - float(t0["CE"])
    except (KeyError, TypeError, ValueError):
        return None
    return (d_pe - d_ce) / base


def combine_flow(per_index: dict[str, float | None], deadband: float = 0.0) -> dict:
    """Mean flow over indices with data → {flow, side (CE|PE|None), per_index, n}."""
    vals = [v for v in per_index.values() if v is not None]
    if not vals:
        return {"flow": None, "side": None, "per_index": per_index, "n": 0}
    flow = sum(vals) / len(vals)
    side = None
    if flow > deadband:
        side = "CE"
    elif flow < -deadband:
        side = "PE"
    return {"flow": round(flow, 6), "side": side,
            "per_index": {k: (round(v, 6) if v is not None else None) for k, v in per_index.items()},
            "n": len(vals)}


_SUMS_SQL = text(
    """
    WITH snaps AS (
        SELECT symbol, expiry_date, option_type, open_interest,
               (timestamp AT TIME ZONE 'Asia/Kolkata') AS ts_ist
        FROM oi_snapshots
        WHERE symbol = ANY(:symbols)
          AND option_type IN ('CE', 'PE')
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :d
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::time BETWEEN :t0 AND :t1
    ), nearest AS (
        SELECT symbol, MIN(expiry_date) AS exp FROM snaps WHERE expiry_date >= :d GROUP BY symbol
    )
    SELECT s.symbol, to_char(s.ts_ist, 'HH24:MI') AS hm, s.option_type, SUM(s.open_interest) AS oi
    FROM snaps s JOIN nearest n ON n.symbol = s.symbol AND n.exp = s.expiry_date
    GROUP BY s.symbol, hm, s.option_type
    ORDER BY s.symbol, hm
    """
)


async def fetch_oi_sums(
    session: AsyncSession, d: date, symbols: list[str], t0: time = OI_T0, t1: time = OI_T1,
) -> dict[str, dict[str, dict[str, float]]]:
    """{index: {HH:MM: {CE: oi_sum, PE: oi_sum}}} for nearest-expiry snapshots in [t0, t1]."""
    rows = (await session.execute(
        _SUMS_SQL, {"symbols": symbols, "d": d, "t0": t0, "t1": t1}
    )).all()
    out: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        out.setdefault(r.symbol, {}).setdefault(r.hm, {})[r.option_type] = float(r.oi)
    return out


def flow_from_sums(
    sums: dict[str, dict[str, dict[str, float]]],
    t0: str = "09:16",
    upto: str | None = None,
    deadband: float = 0.0,
) -> dict:
    """Flow from `fetch_oi_sums` output: t0 snapshot vs the latest snapshot <= `upto` (> t0)."""
    per: dict[str, float | None] = {}
    t1_used: dict[str, str] = {}
    for idx, by_min in sums.items():
        a = by_min.get(t0)
        later = sorted(m for m in by_min if m > t0 and (upto is None or m <= upto)
                       and {"CE", "PE"} <= set(by_min[m]))
        if not a or not {"CE", "PE"} <= set(a) or not later:
            per[idx] = None
            continue
        t1_used[idx] = later[-1]
        per[idx] = index_flow(a, by_min[later[-1]])
    res = combine_flow(per, deadband)
    res["t0"] = t0
    res["t1"] = t1_used
    return res


async def opening_oi_flow(
    session: AsyncSession, d: date, symbols: list[str], now: datetime | time | None = None,
    deadband: float = 0.0,
) -> dict:
    """Opening OI flow for `d` as of `now` (default: the full 09:16→09:19 window)."""
    upto_t = OI_T1
    if now is not None:
        nt = now.time() if isinstance(now, datetime) else now
        upto_t = min(nt, OI_T1)
    sums = await fetch_oi_sums(session, d, symbols, OI_T0, upto_t)
    return flow_from_sums(sums, OI_T0.strftime("%H:%M"), upto_t.strftime("%H:%M"), deadband)
