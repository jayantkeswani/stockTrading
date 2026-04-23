import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import TradeStatus
from app.core.utils import now_ist
from app.models.trade import Trade
from app.schemas.trade import TradeCloseRequest, TradeResponse, TradeSummaryResponse

router = APIRouter()


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    status: str | None = None,
    strategy: str | None = None,
    closed_since: datetime | None = None,
    entry_since: datetime | None = None,
    entry_until: datetime | None = None,
    limit: int = Query(default=50, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade)
    if status:
        query = query.where(Trade.status == status)
    if strategy:
        query = query.where(Trade.strategy_name == strategy)
    if closed_since is not None:
        query = query.where(Trade.exit_time >= closed_since)
        query = query.order_by(desc(Trade.exit_time))
    else:
        if entry_since is not None:
            query = query.where(Trade.entry_time >= entry_since)
        if entry_until is not None:
            query = query.where(Trade.entry_time <= entry_until)
        query = query.order_by(desc(Trade.entry_time))
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/summary", response_model=TradeSummaryResponse)
async def trade_summary(db: AsyncSession = Depends(get_db)):
    closed = await db.execute(
        select(Trade).where(Trade.status == TradeStatus.CLOSED)
    )
    trades = closed.scalars().all()
    if not trades:
        return TradeSummaryResponse(
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0,
            total_pnl=0, avg_pnl=0, avg_winner=0, avg_loser=0,
            best_trade=0, worst_trade=0, profit_factor=0,
        )

    winners = [t for t in trades if t.pnl and t.pnl > 0]
    losers = [t for t in trades if t.pnl and t.pnl < 0]
    total_pnl = sum(t.pnl for t in trades if t.pnl)
    gross_profit = sum(t.pnl for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0

    return TradeSummaryResponse(
        total_trades=len(trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=len(winners) / len(trades) * 100 if trades else 0,
        total_pnl=total_pnl,
        avg_pnl=total_pnl / len(trades) if trades else 0,
        avg_winner=gross_profit / len(winners) if winners else 0,
        avg_loser=-gross_loss / len(losers) if losers else 0,
        best_trade=max((t.pnl for t in trades if t.pnl), default=0),
        worst_trade=min((t.pnl for t in trades if t.pnl), default=0),
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else 0,
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.post("/{trade_id}/close", response_model=TradeResponse)
async def close_trade(
    trade_id: uuid.UUID,
    body: TradeCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != TradeStatus.OPEN:
        raise HTTPException(status_code=400, detail="Trade is not open")

    trade.status = TradeStatus.CLOSED
    trade.exit_reason = body.reason
    trade.exit_time = now_ist()
    if body.exit_price:
        trade.exit_price = body.exit_price
        trade.pnl = (body.exit_price - trade.entry_price) * trade.quantity
        trade.pnl_percent = (
            (body.exit_price - trade.entry_price) / trade.entry_price * 100
        )

    await db.flush()
    return trade
