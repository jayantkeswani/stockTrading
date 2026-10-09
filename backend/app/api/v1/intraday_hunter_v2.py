"""Intraday Hunter v2 API — mounted under /api/v1/intraday-hunter (paths /v2/* and /teacher/*).

v1's router (intraday_hunter.py) is untouched; these are the v2 equivalents + the learning loop:
today / history / run/{date} / basket / emergency basket close / manual Call 1+2 / grades /
ledger / weekly reviews / minute log, and the teacher day + the teacher ingest push.
"""
import logging
import uuid
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.utils import now_ist
from app.schemas.intraday_hunter import IntradayHunterHistoryItem, IntradayHunterRunResponse
from app.schemas.intraday_hunter_v2 import (
    IhDayGradeResponse,
    IhTeacherDayResponse,
    IhTeacherIngestRequest,
    IhV2BasketResponse,
    IhV2Book,
    IhV2Leg,
    IhV2ProposalAction,
    IhV2ProposalResponse,
    IhWeeklyReviewResponse,
)
from app.services.intraday_hunter import store
from app.services.intraday_hunter_v2.params import STRATEGY, VARIANT, v2_params_async

logger = logging.getLogger(__name__)
router = APIRouter()


# ── runs ──
@router.get("/v2/today", response_model=IntradayHunterRunResponse)
async def v2_today(db: AsyncSession = Depends(get_db)):
    """Today's v2 run (Call 1 plan + latest Call 2 + full decision log). PENDING stub if none."""
    today = now_ist().date()
    run = await store.get_run(db, today, VARIANT)
    return IntradayHunterRunResponse.from_run(run) if run else IntradayHunterRunResponse.pending_stub(today)


@router.get("/v2/history", response_model=list[IntradayHunterHistoryItem])
async def v2_history(limit: int = Query(30, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    """Prior v2 days, newest first."""
    return [IntradayHunterHistoryItem.from_run(r) for r in await store.history(db, limit, VARIANT)]


@router.get("/v2/run/{run_date}", response_model=IntradayHunterRunResponse)
async def v2_run(run_date: date, db: AsyncSession = Depends(get_db)):
    """Full v2 run for a date (404 if none)."""
    run = await store.get_run(db, run_date, VARIANT)
    if run is None:
        raise HTTPException(status_code=404, detail="no v2 run for that date")
    return IntradayHunterRunResponse.from_run(run)


@router.post("/v2/run-call1", response_model=IntradayHunterRunResponse)
async def v2_run_call1(run_date: date | None = Query(None), db: AsyncSession = Depends(get_db)):
    """Manually (re)run v2 Call 1 inline (Opus + charts — may take ~40s)."""
    from app.services.intraday_hunter_v2 import thesis

    run = await thesis.run_call1(db, run_date or now_ist().date())
    await db.commit()
    await db.refresh(run)  # server-generated timestamps (avoid a lazy load in the serializer)
    return IntradayHunterRunResponse.from_run(run)


@router.post("/v2/run-call2", response_model=IntradayHunterRunResponse)
async def v2_run_call2(
    run_date: date | None = Query(None),
    at: str | None = Query(None, description="decision time HH:MM IST (default now)"),
    db: AsyncSession = Depends(get_db),
):
    """Manually force one v2 Call 2 (ENTER emits signals exactly like the watcher)."""
    from app.services.intraday_hunter_v2 import decision

    try:
        now_t = datetime.strptime(at, "%H:%M").time() if at else now_ist().time().replace(second=0, microsecond=0)
    except ValueError:
        raise HTTPException(status_code=400, detail="`at` must be HH:MM")
    run = await decision.run_call2(db, run_date or now_ist().date(), now=now_t)
    await db.commit()
    if run is None:
        raise HTTPException(status_code=409, detail="no opening data yet for that date/time")
    await db.refresh(run)
    return IntradayHunterRunResponse.from_run(run)


# ── basket ──
def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


@router.get("/v2/basket", response_model=IhV2BasketResponse)
async def v2_basket(db: AsyncSession = Depends(get_db)):
    """Today's v2 baskets per book: basket-level MTM (bid) vs ±T, round-hold state, legs."""
    from app.agent import trade_monitor
    from app.core.enums import TradeSource, TradeStatus
    from app.core.redis import get_cached_price
    from app.models.trade import Trade
    from app.services.intraday_hunter_v2.basket import basket_mtm, basket_target, lots_by_index
    from app.services.yolo_profile_service import get_all_profiles

    params = await v2_params_async()
    pct = float(params["basket_tp_sl_pct"])
    today = now_ist().date()
    names = {str(p.id): p.name for p in await get_all_profiles()}
    books: list[IhV2Book] = []

    for key, legs in (await trade_monitor._ih_v2_open_baskets(db)).items():
        quotes, _spots = await trade_monitor._ih_v2_quotes(legs)
        mtm, cost = basket_mtm(quotes)
        state = trade_monitor._ih_v2_basket_state.get(key)
        T = basket_target(params, cost, lots_by_index(quotes))
        out_legs = []
        for (pos, trade), q in zip(legs, quotes):
            px = q.bid or q.ltp
            out_legs.append(IhV2Leg(
                position_id=pos.id, trade_id=trade.id, index=pos.symbol, option_type=pos.option_type,
                strike=float(pos.strike_price), leg=((trade.signal_snapshot or {}).get("indicators") or {}).get("ih_leg"),
                fyers_option_symbol=pos.fyers_option_symbol, lots=pos.lots, quantity=pos.quantity,
                entry_price=float(pos.entry_price), ltp=q.ltp, bid=q.bid,
                pnl=round((px - q.entry_price) * q.qty, 2) if px else None, status="OPEN",
            ))
        books.append(IhV2Book(
            book="SHADOW" if key[1] == "SHADOW" else names.get(key[1], key[1]),
            is_shadow=key[1] == "SHADOW", direction=legs[0][0].option_type, status="OPEN",
            cost=round(cost, 2), mtm=None if mtm is None else round(mtm, 2), T=round(T, 2),
            pct_of_T=None if (mtm is None or not T) else round(mtm / T * 100, 1),
            round_hold_active=bool(state and state.active),
            round_hold_targets=(state.targets if state and state.active else {}),
            legs=out_legs,
        ))

    closed = (await db.execute(select(Trade).where(
        Trade.strategy_name == STRATEGY, Trade.status == TradeStatus.CLOSED.value,
        cast(func.timezone("Asia/Kolkata", Trade.entry_time), Date) == today,
    ))).scalars().all()
    by_book: dict[str, list] = {}
    for t in closed:
        b = "SHADOW" if t.source == TradeSource.SHADOW.value else names.get(str(t.yolo_profile_id), "YOLO")
        by_book.setdefault(b, []).append(t)
    for b, trades in by_book.items():
        cost = sum(float(t.entry_price) * t.quantity for t in trades)
        net = sum(float(t.net_pnl if t.net_pnl is not None else (t.pnl or 0)) for t in trades)
        lots: dict[str, int] = {}
        for t in trades:
            lots[t.symbol] = lots.get(t.symbol, 0) + int(t.lots or 1)
        T = basket_target(params, cost, lots)
        books.append(IhV2Book(
            book=b, is_shadow=b == "SHADOW", direction=trades[0].option_type, status="CLOSED",
            cost=round(cost, 2), mtm=round(net, 2), T=round(T, 2),
            pct_of_T=round(net / T * 100, 1) if T else None,
            exit_reason=trades[0].exit_reason,
            legs=[IhV2Leg(trade_id=t.id, index=t.symbol, option_type=t.option_type,
                          strike=float(t.strike_price) if t.strike_price is not None else None,
                          leg=((t.signal_snapshot or {}).get("indicators") or {}).get("ih_leg"),
                          fyers_option_symbol=t.fyers_option_symbol, lots=t.lots, quantity=t.quantity,
                          entry_price=float(t.entry_price),
                          ltp=float(t.exit_price) if t.exit_price is not None else None,
                          pnl=float(t.net_pnl) if t.net_pnl is not None else None,
                          status="CLOSED", exit_reason=t.exit_reason) for t in trades],
        ))
    return IhV2BasketResponse(trading_date=today, basket_tp_sl_pct=pct,
                              basket_t_mode=str(params.get("basket_t_mode", "pct")),
                              time_exit=str(params["basket_time_exit"]), books=books)


@router.post("/v2/basket/close")
async def v2_basket_close(db: AsyncSession = Depends(get_db)):
    """Emergency: close ALL open v2 YOLO legs together (MANUAL_BASKET). The shadow basket keeps
    running under the system rules as the counterfactual."""
    from app.agent.trade_monitor import close_ih_v2_basket_manual

    return await close_ih_v2_basket_manual(db)


# ── learning loop ──
@router.get("/v2/grades", response_model=list[IhDayGradeResponse])
async def v2_grades(limit: int = Query(30, ge=1, le=250), db: AsyncSession = Depends(get_db)):
    from app.models.ih_v2 import IhDayGrade

    rows = (await db.execute(select(IhDayGrade).order_by(IhDayGrade.trading_date.desc()).limit(limit))).scalars().all()
    return [IhDayGradeResponse.from_row(r) for r in rows]


@router.get("/v2/grades/{grade_date}", response_model=IhDayGradeResponse)
async def v2_grade(grade_date: date, db: AsyncSession = Depends(get_db)):
    from app.models.ih_v2 import IhDayGrade

    row = await db.get(IhDayGrade, grade_date)
    if row is None:
        raise HTTPException(status_code=404, detail="no grade for that date")
    return IhDayGradeResponse.from_row(row)


@router.post("/v2/grade", response_model=IhDayGradeResponse)
async def v2_grade_now(run_date: date | None = Query(None), db: AsyncSession = Depends(get_db)):
    """(Re)grade a date now (idempotent)."""
    from app.services.intraday_hunter_v2 import grading

    row = await grading.grade_day(db, run_date or now_ist().date())
    if row is None:
        raise HTTPException(status_code=409, detail="no index candles for that date")
    await db.commit()
    await db.refresh(row)
    return IhDayGradeResponse.from_row(row)


@router.get("/v2/ledger")
async def v2_ledger(db: AsyncSession = Depends(get_db)):
    """Rolling per-arm stats over the last 20 and 60 graded trading days."""
    from app.services.intraday_hunter_v2 import grading

    return await grading.ledger(db)


@router.get("/v2/reviews", response_model=list[IhWeeklyReviewResponse])
async def v2_reviews(limit: int = Query(10, ge=1, le=52), db: AsyncSession = Depends(get_db)):
    from app.models.ih_v2 import IhWeeklyReview

    rows = (await db.execute(select(IhWeeklyReview).order_by(IhWeeklyReview.week_ending.desc()).limit(limit))).scalars().all()
    return [IhWeeklyReviewResponse.from_row(r) for r in rows]


async def _proposal_out(db: AsyncSession, row) -> IhV2ProposalResponse:
    from app.models.ih_v2 import IhWeeklyReview
    from app.services.intraday_hunter_v2 import apply

    review = await db.get(IhWeeklyReview, row.review_id)
    stats = await apply.stats_for(db, row)
    return IhV2ProposalResponse.from_row(
        row, week_ending=review.week_ending if review else None, stats=stats,
        actions=apply.allowed_actions(row.status, stats, apply.agent_running(row.id)),
    )


@router.get("/v2/proposals", response_model=list[IhV2ProposalResponse])
async def v2_proposals(
    review_id: uuid.UUID | None = Query(None), status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
):
    """Weekly-review proposals (newest week first), each with its status chain, apply plan,
    challenger stats and the actions the user may take now."""
    from app.models.ih_v2 import IhV2Proposal, IhWeeklyReview

    q = (select(IhV2Proposal).join(IhWeeklyReview, IhWeeklyReview.id == IhV2Proposal.review_id)
         .order_by(IhWeeklyReview.week_ending.desc(), IhV2Proposal.idx).limit(limit))
    if review_id:
        q = q.where(IhV2Proposal.review_id == review_id)
    if status:
        q = q.where(IhV2Proposal.status == status.upper())
    rows = (await db.execute(q)).scalars().all()
    return [await _proposal_out(db, r) for r in rows]


@router.post("/v2/proposals/{proposal_id}/{action}", response_model=IhV2ProposalResponse)
async def v2_proposal_action(
    proposal_id: uuid.UUID, action: str, body: IhV2ProposalAction | None = None,
    db: AsyncSession = Depends(get_db),
):
    """User click on a proposal: approve | reject | analyse | promote | retire.

    approve (and analyse = re-run) start the apply agent in the background (Opus re-checks the
    evidence and classifies the change; poll GET /v2/proposals). promote merges the challenger's
    params into the champion; it and retire are refused until the challenger stats allow them.
    """
    from app.models.ih_v2 import IhV2Proposal
    from app.services.intraday_hunter_v2 import apply

    if action not in apply.TRANSITIONS:
        raise HTTPException(404, f"unknown action {action}")
    row = await db.get(IhV2Proposal, proposal_id)
    if row is None:
        raise HTTPException(404, "proposal not found")
    note = body.note if body else None
    if apply.agent_running(row.id):
        raise HTTPException(409, "the apply agent is already running for this proposal")
    try:
        if action in ("approve", "reject"):
            await apply.decide(db, row, action, note)
        elif action == "analyse":
            apply.check_transition(row, "analyse")
            if row.status == "NEEDS_REVIEW":  # back to APPROVED so the UI shows the agent working
                await apply.decide_reanalyse(db, row, note)
        elif action == "promote":
            await apply.promote(db, row, note)
        else:
            await apply.retire(db, row, note)
    except apply.TransitionError as e:
        raise HTTPException(409, str(e)) from e
    await db.commit()
    await db.refresh(row)
    if action in ("approve", "analyse"):
        apply.start_apply_agent(row.id)
    return await _proposal_out(db, row)


@router.get("/v2/minute-log")
async def v2_minute_log(
    log_date: date | None = Query(None), index: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """The learning dataset rows for a date (optionally one index), oldest first."""
    from app.models.ih_v2 import IhMinuteLog

    q = select(IhMinuteLog).where(IhMinuteLog.trading_date == (log_date or now_ist().date()))
    if index:
        q = q.where(IhMinuteLog.index == index.upper())
    rows = (await db.execute(q.order_by(IhMinuteLog.minute_ts, IhMinuteLog.index))).scalars().all()
    return [{"minute_ts": r.minute_ts, "index": r.index, "variant": r.variant,
             "features": r.features, "arms": r.arms} for r in rows]


# ── teacher ──
@router.get("/teacher/{teacher_date}", response_model=IhTeacherDayResponse)
async def teacher_day(teacher_date: date, db: AsyncSession = Depends(get_db)):
    """The teacher's plan + actual live trade for a date (404 if nothing ingested)."""
    from app.services.intraday_hunter_v2.teacher.ingest import get_teacher_day

    row = await get_teacher_day(db, teacher_date)
    if row is None:
        raise HTTPException(status_code=404, detail="no teacher data for that date")
    return IhTeacherDayResponse.from_row(row)


@router.post("/teacher/ingest", response_model=IhTeacherDayResponse)
async def teacher_ingest(body: IhTeacherIngestRequest, db: AsyncSession = Depends(get_db)):
    """Push teacher plan/live results computed elsewhere (e.g. the Mac when the server IP is
    blocked by YouTube). Normalized the same way as the server jobs. Re-grades the day when a
    live trade arrives for a date that was already graded."""
    from app.models.ih_v2 import IhDayGrade
    from app.services.intraday_hunter_v2.teacher.ingest import upsert_teacher_day

    if body.plan is None and body.live is None:
        raise HTTPException(status_code=400, detail="nothing to ingest (plan and live are empty)")
    row = await upsert_teacher_day(
        db, body.trading_date, plan=body.plan, live=body.live,
        plan_video_id=body.plan_video_id, live_video_id=body.live_video_id, source="mac_push",
    )
    await db.commit()
    if body.live is not None and await db.get(IhDayGrade, body.trading_date) is not None:
        from app.services.intraday_hunter_v2 import grading
        try:
            await grading.grade_day(db, body.trading_date)
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("ih_v2: re-grade after teacher ingest failed")
    await db.refresh(row)
    return IhTeacherDayResponse.from_row(row)
