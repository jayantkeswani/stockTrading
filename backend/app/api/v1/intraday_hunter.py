"""Intraday Hunter agent API — today's run, history, manual triggers, and chart images.

The agent SUGGESTS index-options trades for a human (MANUAL-alert only); these endpoints
surface the thesis + decision and let the UI poll. `run-call1` / `run-call2` are manual
testing triggers (they make the LLM call inline and can take tens of seconds).
"""
import logging
import os
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.utils import now_ist
from app.schemas.intraday_hunter import (
    IntradayHunterBasketLeg,
    IntradayHunterHistoryItem,
    IntradayHunterRunResponse,
)
from app.services.intraday_hunter import decision as decision_svc
from app.services.intraday_hunter import store, thesis
from app.services.intraday_hunter.data import CHART_DIR

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/today", response_model=IntradayHunterRunResponse)
async def get_today(db: AsyncSession = Depends(get_db)):
    """Today's run: thesis + latest decision + chart URLs + status. PENDING stub if no row."""
    today = now_ist().date()
    run = await store.get_run(db, today)
    if run is None:
        return IntradayHunterRunResponse.pending_stub(today)
    return IntradayHunterRunResponse.from_run(run)


@router.get("/history", response_model=list[IntradayHunterHistoryItem])
async def get_history(
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Prior days (newest first): decision, direction, confidence, trapped side, outcome."""
    runs = await store.history(db, limit=limit)
    return [IntradayHunterHistoryItem.from_run(r) for r in runs]


@router.get("/run/{run_date}", response_model=IntradayHunterRunResponse)
async def get_run_by_date(run_date: date, db: AsyncSession = Depends(get_db)):
    """Full run for a specific past trading date (thesis + decision + chart URLs). 404 if none.

    Backs the History timeline's expand-to-popup detail view.
    """
    run = await store.get_run(db, run_date)
    if run is None:
        raise HTTPException(status_code=404, detail="no run for that date")
    return IntradayHunterRunResponse.from_run(run)


@router.post("/run-call1", response_model=IntradayHunterRunResponse)
async def run_call1_endpoint(
    run_date: date | None = Query(None, description="trading date (default today)"),
    db: AsyncSession = Depends(get_db),
):
    """Manually (re)run Call 1 (pre-open thesis). Makes the LLM call inline — may take ~40s."""
    d = run_date or now_ist().date()
    run = await thesis.run_call1(db, d, variant=settings.intraday_hunter_variant)
    await db.commit()
    return IntradayHunterRunResponse.from_run(run)


@router.post("/run-call2", response_model=IntradayHunterRunResponse)
async def run_call2_endpoint(
    run_date: date | None = Query(None, description="trading date (default today)"),
    at: str | None = Query(None, description="decision clock time HH:MM IST (default now)"),
    db: AsyncSession = Depends(get_db),
):
    """Manually force a Call 2 decision. Requires a thesis (run-call1 first). Inline LLM call."""
    d = run_date or now_ist().date()
    now_t: time | None = None
    if at:
        try:
            now_t = datetime.strptime(at, "%H:%M").time()
        except ValueError:
            raise HTTPException(status_code=400, detail="`at` must be HH:MM (e.g. 09:18)")
    run = await decision_svc.run_call2(db, d, now=now_t, variant=settings.intraday_hunter_variant)
    await db.commit()
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="No thesis for this date — run Call 1 first (or no opening data yet).",
        )
    return IntradayHunterRunResponse.from_run(run)


async def _live_ltp(symbol: str | None) -> float | None:
    """Live LTP from Redis for a symbol (option contract or index short name)."""
    if not symbol:
        return None
    from app.core.redis import get_cached_price

    data = await get_cached_price(symbol)
    ltp = data.get("ltp") if data else None
    return float(ltp) if ltp and float(ltp) > 0 else None


@router.get("/basket", response_model=list[IntradayHunterBasketLeg])
async def get_basket(db: AsyncSession = Depends(get_db)):
    """Today's Intraday Hunter basket legs (open + closed-today) for the manual close card.

    Open legs carry the live option LTP + index spot vs the index SL/target (informational
    only — each leg is actually exited by its own option-premium SL/target via the normal
    per-position check); closed-today legs carry the realized P&L + exit reason. `position_id`
    (open legs only) lets the UI close a leg.
    """
    from sqlalchemy import Date, cast, func, select

    from app.core.enums import StrategyName, TradeSource, TradeStatus
    from app.models.position import Position
    from app.models.trade import Trade
    from app.services.yolo_profile_service import get_all_profiles

    today = now_ist().date()
    profiles = await get_all_profiles()
    name_by_id = {p.id: p.name for p in profiles}

    def _book(is_shadow: bool, profile_id) -> str:
        if is_shadow:
            return "SHADOW"
        return name_by_id.get(profile_id, "YOLO")

    def _indicators(trade) -> dict:
        snap = trade.signal_snapshot or {}
        return (snap.get("indicators") or {}) if isinstance(snap, dict) else {}

    legs: list[IntradayHunterBasketLeg] = []

    # Open legs (positions exist only while open).
    open_rows = (
        await db.execute(
            select(Position, Trade)
            .join(Trade, Position.trade_id == Trade.id)
            .where(Position.strategy_name == StrategyName.INTRADAY_HUNTER.value)
        )
    ).all()
    for pos, trade in open_rows:
        ind = _indicators(trade)
        live_opt = await _live_ltp(pos.fyers_option_symbol)
        legs.append(IntradayHunterBasketLeg(
            position_id=pos.id,
            trade_id=pos.trade_id,
            book=_book(pos.is_shadow, pos.yolo_profile_id),
            is_shadow=bool(pos.is_shadow),
            index=pos.symbol,
            option_type=pos.option_type,
            strike=float(pos.strike_price) if pos.strike_price is not None else None,
            itm_depth=ind.get("ih_itm_depth"),
            fyers_option_symbol=pos.fyers_option_symbol,
            lots=pos.lots,
            entry_price=float(pos.entry_price) if pos.entry_price is not None else None,
            current_price=live_opt if live_opt is not None else (
                float(pos.current_price) if pos.current_price is not None else None),
            unrealized_pnl=float(pos.unrealized_pnl) if pos.unrealized_pnl is not None else None,
            status="OPEN",
            index_sl=ind.get("index_sl"),
            index_target=ind.get("index_target"),
            index_spot=await _live_ltp(pos.symbol),
        ))

    # Closed-today legs (the exit cue) — positions are deleted on close, so read from trades.
    closed_rows = (
        await db.execute(
            select(Trade).where(
                Trade.strategy_name == StrategyName.INTRADAY_HUNTER.value,
                Trade.status == TradeStatus.CLOSED.value,
                cast(func.timezone("Asia/Kolkata", Trade.entry_time), Date) == today,
            )
        )
    ).scalars().all()
    for trade in closed_rows:
        ind = _indicators(trade)
        is_shadow = trade.source == TradeSource.SHADOW.value
        legs.append(IntradayHunterBasketLeg(
            trade_id=trade.id,
            book=_book(is_shadow, trade.yolo_profile_id),
            is_shadow=is_shadow,
            index=trade.symbol,
            option_type=trade.option_type,
            strike=float(trade.strike_price) if trade.strike_price is not None else None,
            itm_depth=ind.get("ih_itm_depth"),
            fyers_option_symbol=trade.fyers_option_symbol,
            lots=trade.lots,
            entry_price=float(trade.entry_price) if trade.entry_price is not None else None,
            current_price=float(trade.exit_price) if trade.exit_price is not None else None,
            realized_pnl=float(trade.net_pnl) if trade.net_pnl is not None else (
                float(trade.pnl) if trade.pnl is not None else None),
            status="CLOSED",
            exit_reason=trade.exit_reason,
            index_sl=ind.get("index_sl"),
            index_target=ind.get("index_target"),
        ))

    return legs


def _resolve_chart_path(run, which: str) -> str | None:
    """Map a `which` key (e.g. 'prevday_NIFTY' / 'opening_BANKNIFTY') to a stored PNG path."""
    kind, _, idx = which.partition("_")
    if not idx or kind not in ("prevday", "opening"):
        return None
    c2 = run.call2_chart_paths or {}
    if kind == "prevday":
        path = (c2.get("prevday") or {}).get(idx) or (run.call1_chart_paths or {}).get(idx)
    else:
        path = (c2.get("opening") or {}).get(idx)
    if not path:
        return None
    # Defense-in-depth: only ever serve files under CHART_DIR (paths are ours, but guard anyway).
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(CHART_DIR)) or not os.path.isfile(abs_path):
        return None
    return abs_path


@router.get("/chart/{run_id}/{which}")
async def get_chart(run_id: str, which: str, db: AsyncSession = Depends(get_db)):
    """Serve a rendered chart PNG (`prevday_{INDEX}` / `opening_{INDEX}`) for a run."""
    from app.models.intraday_hunter_run import IntradayHunterRun
    from sqlalchemy import select

    res = await db.execute(
        select(IntradayHunterRun).where(IntradayHunterRun.id == run_id)
    )
    run = res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    path = _resolve_chart_path(run, which)
    if path is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return FileResponse(path, media_type="image/png")
