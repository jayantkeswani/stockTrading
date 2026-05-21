from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.trading_config import get_trading_config
from app.core.enums import TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.daily_summary import DailySummary
from app.models.position import Position
from app.models.trade import Trade
from app.schemas.risk import RiskDashboardResponse

router = APIRouter()


@router.get("/dashboard", response_model=RiskDashboardResponse)
async def risk_dashboard(db: AsyncSession = Depends(get_db)):
    cfg = await get_trading_config()
    today = now_ist().date()
    capital = Decimal(cfg.capital)

    # Today's closed trades P&L — shadow trades excluded
    result = await db.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0))
        .where(Trade.status == TradeStatus.CLOSED)
        .where(func.date(Trade.entry_time) == today)
        .where(Trade.source != TradeSource.SHADOW.value)
    )
    daily_pnl = result.scalar() or Decimal(0)

    # Today's trade count — shadow trades excluded
    result = await db.execute(
        select(func.count(Trade.id))
        .where(func.date(Trade.entry_time) == today)
        .where(Trade.source != TradeSource.SHADOW.value)
    )
    trades_today = result.scalar() or 0

    # Open positions — shadow positions excluded
    result = await db.execute(
        select(func.count(Position.id)).where(Position.is_shadow == False)  # noqa: E712
    )
    positions_open = result.scalar() or 0

    # Unrealized P&L from open positions — shadow positions excluded
    result = await db.execute(
        select(func.coalesce(func.sum(Position.unrealized_pnl), 0))
        .where(Position.is_shadow == False)  # noqa: E712
    )
    unrealized = result.scalar() or Decimal(0)

    total_daily_pnl = daily_pnl + unrealized
    drawdown_pct = float(abs(min(total_daily_pnl, 0)) / capital * 100)

    # Notional exposure — entry_price * quantity for open non-shadow positions
    result = await db.execute(
        select(func.coalesce(func.sum(Position.entry_price * Position.quantity), 0))
        .where(Position.is_shadow == False)  # noqa: E712
    )
    notional = result.scalar() or Decimal(0)

    # SL-based risk — |entry - SL| * quantity for open non-shadow positions
    result = await db.execute(
        select(func.coalesce(
            func.sum(func.abs(Position.entry_price - Position.stop_loss) * Position.quantity),
            0,
        ))
        .where(Position.is_shadow == False)  # noqa: E712
    )
    risk = result.scalar() or Decimal(0)

    # Margin utilized — sum of margin_required for open non-shadow positions
    result = await db.execute(
        select(func.coalesce(func.sum(Position.margin_required), 0))
        .where(Position.is_shadow == False)  # noqa: E712
    )
    margin_utilized = result.scalar() or Decimal(0)

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
        positions_open=positions_open,
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
