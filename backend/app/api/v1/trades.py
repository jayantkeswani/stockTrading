import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import TradeSource, TradeStatus
from app.core.redis import get_cached_price
from app.core.utils import now_ist
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.trade import TradeCloseRequest, TradeResponse, TradeSummaryResponse
from app.websocket.manager import ws_manager

router = APIRouter()


def _to_response(trade: Trade, signal: Signal | None) -> TradeResponse:
    resp = TradeResponse.model_validate(trade)
    if signal:
        resp.signal_confidence = signal.confidence
        resp.signal_ai_action = signal.ai_action
        resp.signal_ai_summary = signal.ai_summary
        resp.signal_instrument_type = signal.instrument_type
        resp.signal_type = signal.signal_type
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
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).where(Trade.status == TradeStatus.CLOSED)
    if source:
        query = query.where(Trade.source == source)
    else:
        query = query.where(Trade.source != TradeSource.SHADOW.value)
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


@router.post("/close-all")
async def close_all_trades(
    body: TradeCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
    open_trades = result.scalars().all()
    if not open_trades:
        return {"closed": 0, "trades": []}

    closed = []
    for trade in open_trades:
        exit_price = None
        price_symbol = trade.fyers_option_symbol or trade.symbol
        price_data = await get_cached_price(price_symbol)
        if price_data:
            ltp = Decimal(str(price_data.get("ltp", 0)))
            if ltp > 0:
                exit_price = ltp

        trade.status = TradeStatus.CLOSED
        trade.exit_reason = body.reason
        trade.exit_time = now_ist()
        if exit_price:
            trade.exit_price = exit_price
            is_short = trade.side == "SELL"
            diff = (trade.entry_price - exit_price) if is_short else (exit_price - trade.entry_price)
            trade.pnl = diff * trade.quantity
            trade.pnl_percent = float(diff / trade.entry_price * 100)

        pos_result = await db.execute(select(Position).where(Position.trade_id == trade.id))
        position = pos_result.scalar_one_or_none()
        position_id = None
        if position:
            position_id = str(position.id)
            await db.delete(position)

        closed.append({
            "trade_id": str(trade.id),
            "position_id": position_id,
            "symbol": trade.symbol,
            "exit_price": float(trade.exit_price) if trade.exit_price else 0.0,
            "pnl": float(trade.pnl) if trade.pnl else 0.0,
        })

    await db.flush()

    for item in closed:
        await ws_manager.broadcast("position:closed", item)

    return {"closed": len(closed), "trades": closed}


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

    exit_price = body.exit_price
    if not exit_price:
        price_symbol = trade.fyers_option_symbol or trade.symbol
        price_data = await get_cached_price(price_symbol)
        if price_data:
            ltp = Decimal(str(price_data.get("ltp", 0)))
            if ltp > 0:
                exit_price = ltp

    trade.status = TradeStatus.CLOSED
    trade.exit_reason = body.reason
    trade.exit_time = now_ist()
    if exit_price:
        trade.exit_price = exit_price
        is_short = trade.side == "SELL"
        diff = (trade.entry_price - exit_price) if is_short else (exit_price - trade.entry_price)
        trade.pnl = diff * trade.quantity
        trade.pnl_percent = float(diff / trade.entry_price * 100)

    # Delete linked position
    pos_result = await db.execute(select(Position).where(Position.trade_id == trade_id))
    position = pos_result.scalar_one_or_none()
    position_id = None
    if position:
        position_id = str(position.id)
        await db.delete(position)

    await db.flush()

    await ws_manager.broadcast(
        "position:closed",
        {
            "position_id": position_id,
            "trade_id": str(trade_id),
            "exit_price": float(trade.exit_price) if trade.exit_price else 0.0,
            "pnl": float(trade.pnl) if trade.pnl else 0.0,
        },
    )
    return trade
