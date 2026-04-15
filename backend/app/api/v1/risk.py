from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.core.enums import TradeStatus
from app.core.utils import now_ist
from app.models.daily_summary import DailySummary
from app.models.position import Position
from app.models.trade import Trade
from app.schemas.risk import RiskDashboardResponse

router = APIRouter()


@router.get("/dashboard", response_model=RiskDashboardResponse)
async def risk_dashboard(db: AsyncSession = Depends(get_db)):
    today = now_ist().date()
    capital = Decimal(settings.trading_capital)

    # Today's closed trades P&L
    result = await db.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0))
        .where(Trade.status == TradeStatus.CLOSED)
        .where(func.date(Trade.entry_time) == today)
    )
    daily_pnl = result.scalar() or Decimal(0)

    # Today's trade count
    result = await db.execute(
        select(func.count(Trade.id))
        .where(func.date(Trade.entry_time) == today)
    )
    trades_today = result.scalar() or 0

    # Open positions
    result = await db.execute(select(func.count(Position.id)))
    positions_open = result.scalar() or 0

    # Unrealized P&L from open positions
    result = await db.execute(
        select(func.coalesce(func.sum(Position.unrealized_pnl), 0))
    )
    unrealized = result.scalar() or Decimal(0)

    total_daily_pnl = daily_pnl + unrealized
    drawdown_pct = float(abs(min(total_daily_pnl, 0)) / capital * 100)

    # Capital at risk (sum of entry_price * quantity for open positions)
    result = await db.execute(
        select(func.coalesce(func.sum(Position.entry_price * Position.quantity), 0))
    )
    capital_at_risk = result.scalar() or Decimal(0)

    return RiskDashboardResponse(
        capital=capital,
        daily_pnl=total_daily_pnl,
        daily_drawdown_pct=drawdown_pct,
        max_daily_drawdown_pct=settings.max_daily_drawdown_pct,
        trades_today=trades_today,
        max_trades_per_day=settings.max_trades_per_day,
        capital_at_risk=capital_at_risk,
        is_halted=drawdown_pct >= settings.max_daily_drawdown_pct,
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
