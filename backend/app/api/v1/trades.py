import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import TradeSource, TradeStatus
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.trade import TradeResponse, TradeSummaryResponse

router = APIRouter()


def _to_response(trade: Trade, signal: Signal | None) -> TradeResponse:
    resp = TradeResponse.model_validate(trade)
    if signal:
        resp.signal_confidence = signal.confidence
        resp.signal_ai_action = signal.ai_action
        resp.signal_ai_summary = signal.ai_summary
        resp.signal_instrument_type = signal.instrument_type
        resp.signal_type = signal.signal_type
        resp.signal_is_permanent_watchlist = signal.is_permanent_watchlist
    return resp


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    status: str | None = None,
    strategy: str | None = None,
    source: str | None = None,
    closed_since: datetime | None = None,
    entry_since: datetime | None = None,
    entry_until: datetime | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    ai_action: str | None = None,
    instrument_type: str | None = None,
    signal_type: str | None = None,
    min_lots: int | None = None,
    max_lots: int | None = None,
    exclude_permanent: bool | None = None,
    limit: int = Query(default=50, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade, Signal).outerjoin(Signal, Trade.signal_id == Signal.id)
    # Default: exclude shadow trades; pass source="SHADOW" to see only shadows
    if source:
        query = query.where(Trade.source == source)
    else:
        query = query.where(Trade.source != TradeSource.SHADOW.value)
    if status:
        query = query.where(Trade.status == status)
    if strategy:
        query = query.where(Trade.strategy_name == strategy)
    if min_lots is not None:
        query = query.where(Trade.lots >= min_lots)
    if max_lots is not None:
        query = query.where(Trade.lots <= max_lots)
    if exclude_permanent:
        query = query.where(Trade.is_permanent_watchlist == False)  # noqa: E712
    # Signal-level simulation filters (trades with no linked signal are excluded when these are set)
    if min_confidence is not None:
        query = query.where(Signal.confidence >= min_confidence)
    if max_confidence is not None:
        query = query.where(Signal.confidence <= max_confidence)
    if ai_action:
        query = query.where(Signal.ai_action == ai_action)
    if instrument_type:
        query = query.where(Signal.instrument_type == instrument_type)
    if signal_type:
        query = query.where(Signal.signal_type == signal_type)
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
    return [_to_response(trade, signal) for trade, signal in result.all()]


@router.get("/summary", response_model=TradeSummaryResponse)
async def trade_summary(
    source: str | None = None,
    exclude_permanent: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).where(Trade.status == TradeStatus.CLOSED)
    if source:
        query = query.where(Trade.source == source)
    else:
        query = query.where(Trade.source != TradeSource.SHADOW.value)
    if exclude_permanent:
        query = query.where(Trade.is_permanent_watchlist == False)  # noqa: E712
    closed = await db.execute(query)
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

    total_charges = sum(
        Decimal(str(t.charges_json["total"])) if t.charges_json else Decimal(0)
        for t in trades
    )
    total_net_pnl = sum(
        t.net_pnl if t.net_pnl is not None else (t.pnl or 0)
        for t in trades
    )

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
        total_net_pnl=total_net_pnl,
        total_charges=total_charges,
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


