from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.daily_summary import DailySummary
from app.models.position import Position
from app.models.trade import Trade
from app.schemas.risk import ProfileRiskSummary, RiskDashboardResponse
from app.services.trading_config import get_trading_config
from app.services.yolo_profile_service import get_active_profiles, get_uncapped_profile_ids

router = APIRouter()


@router.get("/dashboard", response_model=RiskDashboardResponse)
async def risk_dashboard(
    yolo_profile_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return risk metrics for the dashboard, optionally scoped to a single YOLO profile."""
    cfg = await get_trading_config()
    today = now_ist().date()
    capital = Decimal(cfg.capital)

    # Today's closed trades P&L — shadow trades excluded; optionally scoped to a profile
    closed_pnl_q = (
        select(func.coalesce(func.sum(Trade.pnl), 0))
        .where(Trade.status == TradeStatus.CLOSED)
        .where(func.date(Trade.entry_time) == today)
        .where(Trade.source != TradeSource.SHADOW.value)
    )
    if yolo_profile_id is not None:
        closed_pnl_q = closed_pnl_q.where(Trade.yolo_profile_id == yolo_profile_id)
    result = await db.execute(closed_pnl_q)
    daily_pnl = result.scalar() or Decimal(0)

    # Today's trade count — shadow trades excluded; optionally scoped to a profile
    trades_count_q = (
        select(func.count(Trade.id))
        .where(func.date(Trade.entry_time) == today)
        .where(Trade.source != TradeSource.SHADOW.value)
    )
    if yolo_profile_id is not None:
        trades_count_q = trades_count_q.where(Trade.yolo_profile_id == yolo_profile_id)
    result = await db.execute(trades_count_q)
    trades_today = result.scalar() or 0

    # Open positions — shadow positions excluded; optionally scoped to a profile
    positions_count_q = select(func.count(Position.id)).where(
        Position.is_shadow == False  # noqa: E712
    )
    if yolo_profile_id is not None:
        positions_count_q = positions_count_q.where(Position.yolo_profile_id == yolo_profile_id)
    result = await db.execute(positions_count_q)
    positions_open = result.scalar() or 0

    # Unrealized P&L from open positions — shadow positions excluded; optionally scoped to a profile
    unrealized_q = select(func.coalesce(func.sum(Position.unrealized_pnl), 0)).where(
        Position.is_shadow == False  # noqa: E712
    )
    if yolo_profile_id is not None:
        unrealized_q = unrealized_q.where(Position.yolo_profile_id == yolo_profile_id)
    result = await db.execute(unrealized_q)
    unrealized = result.scalar() or Decimal(0)

    total_daily_pnl = daily_pnl + unrealized
    drawdown_pct = float(abs(min(total_daily_pnl, 0)) / capital * 100)

    # Notional exposure — entry_price * quantity for open non-shadow positions; optionally scoped
    notional_q = select(
        func.coalesce(func.sum(Position.entry_price * Position.quantity), 0)
    ).where(Position.is_shadow == False)  # noqa: E712
    if yolo_profile_id is not None:
        notional_q = notional_q.where(Position.yolo_profile_id == yolo_profile_id)
    result = await db.execute(notional_q)
    notional = result.scalar() or Decimal(0)

    # SL-based risk — |entry - SL| * quantity for open non-shadow positions; optionally scoped
    risk_q = select(
        func.coalesce(
            func.sum(func.abs(Position.entry_price - Position.stop_loss) * Position.quantity),
            0,
        )
    ).where(Position.is_shadow == False)  # noqa: E712
    if yolo_profile_id is not None:
        risk_q = risk_q.where(Position.yolo_profile_id == yolo_profile_id)
    result = await db.execute(risk_q)
    risk = result.scalar() or Decimal(0)

    # Margin utilized — sum of margin_required for open non-shadow positions; optionally scoped
    margin_q = select(func.coalesce(func.sum(Position.margin_required), 0)).where(
        Position.is_shadow == False  # noqa: E712
    )
    if yolo_profile_id is not None:
        margin_q = margin_q.where(Position.yolo_profile_id == yolo_profile_id)
    result = await db.execute(margin_q)
    margin_utilized = result.scalar() or Decimal(0)

    # Per-profile profit cap status
    active_profiles = await get_active_profiles()
    uncapped_ids = await get_uncapped_profile_ids(today)

    profile_summaries = []
    for prof in active_profiles:
        p_result = await db.execute(
            select(
                func.coalesce(func.sum(Trade.net_pnl), 0),
                func.coalesce(func.sum(Trade.pnl), 0),
            )
            .where(Trade.yolo_profile_id == prof.id)
            .where(Trade.status == TradeStatus.CLOSED)
            .where(func.date(Trade.entry_time) == today)
        )
        p_row = p_result.one()
        p_net = float(p_row[0])
        p_gross = float(p_row[1])
        p_closed = p_net if p_net != 0 or p_gross == 0 else p_gross
        p_unr = await db.execute(
            select(func.coalesce(func.sum(Position.unrealized_pnl), 0))
            .where(Position.yolo_profile_id == prof.id)
        )
        p_total = p_closed + float(p_unr.scalar() or 0)
        profile_summaries.append(ProfileRiskSummary(
            id=prof.id,
            name=prof.name,
            profit_cap=prof.profit_cap,
            current_pnl=round(p_total, 0),
            is_capped=prof.id not in uncapped_ids,
        ))

    if yolo_profile_id is not None:
        matched = next((p for p in active_profiles if str(p.id) == yolo_profile_id), None)
        all_capped = matched is not None and matched.id not in uncapped_ids
    else:
        all_capped = bool(active_profiles) and len(uncapped_ids) == 0

    return RiskDashboardResponse(
        capital=capital,
        daily_pnl=total_daily_pnl,
        closed_pnl=daily_pnl,
        daily_drawdown_pct=drawdown_pct,
        max_daily_drawdown_pct=cfg.max_daily_drawdown_pct,
        trades_today=trades_today,
        max_trades_per_day=cfg.max_trades_per_day,
        notional=notional,
        risk=risk,
        margin_utilized=margin_utilized,
        is_halted=drawdown_pct >= cfg.max_daily_drawdown_pct,
        is_profit_capped=all_capped,
        positions_open=positions_open,
        profiles=profile_summaries,
    )


@router.get("/history")
async def risk_history(
    limit: int = Query(default=30, le=90),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DailySummary)
        .order_by(DailySummary.trade_date.desc())
        .limit(limit)
    )
    return result.scalars().all()
