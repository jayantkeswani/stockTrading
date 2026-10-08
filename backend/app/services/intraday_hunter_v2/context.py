"""v2 context assembly (async DB reads) — shared by Call 1, Call 2, the minute log and grading.

Reads candles through v1's `intraday_hunter.data` helpers (same IST/session filters, read-only
reuse — v1 is not modified), the teacher plan from `ih_teacher_days`, graded lessons from
`ih_day_grades`, and composes the pure `levels` facts per index.
"""
from __future__ import annotations

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ih_v2 import IhDayGrade, IhTeacherDay
from app.services.intraday_hunter import context as v1ctx
from app.services.intraday_hunter.data import fetch_day, fetch_vix, prev_trading_date
from app.services.intraday_hunter_v2 import gates as gates_mod
from app.services.intraday_hunter_v2 import levels
from app.services.intraday_hunter_v2.params import parse_hhmm

INDICES = levels.INDICES


async def prev_day_all(session: AsyncSession, d: date) -> dict[str, dict]:
    """{index: v1 prev_day_structure + prev_date} for every index with prior data."""
    out: dict[str, dict] = {}
    for idx in INDICES:
        pd = await prev_trading_date(session, idx, d)
        if pd is None:
            continue
        candles = await fetch_day(session, idx, pd)
        if not candles:
            continue
        st = v1ctx.prev_day_structure(candles, idx)
        st["prev_date"] = pd.isoformat()
        out[idx] = st
    return out


async def today_candles(session: AsyncSession, d: date, upto: time | None = None) -> dict[str, list[dict]]:
    """{index: today's 1m candles up to and including minute `upto` (HH:MM)}."""
    out: dict[str, list[dict]] = {}
    cut = upto.strftime("%H:%M") if upto else "23:59"
    for idx in INDICES:
        out[idx] = [c for c in await fetch_day(session, idx, d) if c["ts"][11:16] <= cut]
    return out


async def get_teacher_day(session: AsyncSession, d: date) -> IhTeacherDay | None:
    return (await session.execute(
        select(IhTeacherDay).where(IhTeacherDay.trading_date == d)
    )).scalar_one_or_none()


def drawn_levels(plan: dict | None, index: str) -> list[float]:
    """The teacher's on-screen (drawn) levels for `index`, merged with levels he spoke."""
    if not plan:
        return []
    out: list[float] = []
    for key in ("levels_onscreen", "levels_audio"):
        for v in (plan.get(key) or {}).get(index, []) or []:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return sorted(set(out))


async def recent_lessons(session: AsyncSession, d: date, n: int) -> list[dict]:
    """The last `n` graded lessons strictly before `d`, oldest first."""
    rows = (await session.execute(
        select(IhDayGrade).where(IhDayGrade.trading_date < d, IhDayGrade.lesson.isnot(None))
        .order_by(IhDayGrade.trading_date.desc()).limit(n)
    )).scalars().all()
    return [r.lesson for r in reversed(rows)]


def facts_by_index(
    prev: dict[str, dict], candles: dict[str, list[dict]], plan: dict | None, params: dict,
) -> dict[str, dict]:
    """Level facts per index (indices with no candles yet are omitted)."""
    steps = params.get("round_step") or {}
    or_end = parse_hhmm(params.get("opening_range_end", "09:19"))
    out: dict[str, dict] = {}
    for idx in INDICES:
        p, cs = prev.get(idx), candles.get(idx) or []
        if not p or not cs:
            continue
        f = levels.level_facts(
            idx, {"close": p["close"], "high": p["high"], "low": p["low"]}, cs,
            drawn_levels=drawn_levels(plan, idx), round_step=steps.get(idx), or_end=or_end,
        )
        if f:
            out[idx] = f
    return out


def opening_of(facts: dict[str, dict], params: dict) -> str:
    """Today's opening type from the basket-average gap."""
    return levels.opening_type(
        {i: f.get("gap_pct") for i, f in facts.items()},
        float(params.get("opening_type_gap_pct", 0.15)),
    )


def plan_side_today(plan: dict | None, opening: str) -> str | None:
    return gates_mod.plan_side_for_opening(plan, opening)


def preopen_levels(prev: dict[str, dict], plan: dict | None, params: dict) -> dict[str, list[dict]]:
    """Pre-open stop-level list per index (relative to the previous close) for Call 1."""
    steps = params.get("round_step") or {}
    out: dict[str, list[dict]] = {}
    for idx, st in prev.items():
        pc = float(st["close"])
        step = float(steps.get(idx) or levels.DEFAULT_ROUND_STEP.get(idx, 100))
        above, below = levels.round_levels(pc, step)
        rows = [("prev_close", pc), ("pdh", float(st["high"])), ("pdl", float(st["low"])),
                ("round_above", above), ("round_below", below)]
        rows += [(f"drawn_{i + 1}", v) for i, v in enumerate(drawn_levels(plan, idx))]
        out[idx] = [{"name": n, "price": round(v, 2), "dist_pts_from_close": round(v - pc, 2),
                     "dist_pct_from_close": round((v - pc) / pc * 100, 3)} for n, v in rows]
    return out


async def vix_now(session: AsyncSession, d: date, upto: time | None = None) -> float | None:
    return await fetch_vix(session, d, upto=upto)
