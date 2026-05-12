import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ExitReason, TradeStatus
from app.core.redis import get_cached_price
from app.core.utils import now_ist
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.position import PositionCloseRequest, PositionResponse, PositionUpdateSLRequest
from app.websocket.manager import ws_manager

router = APIRouter()


def _to_response(pos: Position, signal: Signal | None) -> PositionResponse:
    resp = PositionResponse.model_validate(pos)
    if signal:
        resp.signal_confidence = signal.confidence
    return resp


@router.get("", response_model=list[PositionResponse])
async def list_positions(
    include_shadow: bool = False,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Position, Signal)
        .outerjoin(Trade, Position.trade_id == Trade.id)
        .outerjoin(Signal, Trade.signal_id == Signal.id)
        .order_by(Position.opened_at.desc())
    )
    if not include_shadow:
        query = query.where(Position.is_shadow == False)  # noqa: E712
    result = await db.execute(query)
    rows = result.all()

    responses = []
    for pos, signal in rows:
        # Enrich with live prices from Redis cache
        price_symbol = pos.fyers_option_symbol or pos.symbol
        price_data = await get_cached_price(price_symbol)
        if price_data:
            ltp = Decimal(str(price_data.get("ltp", 0)))
            if ltp > 0:
                pos.current_price = ltp
                is_short = pos.target_price is not None and pos.target_price < pos.entry_price
                diff = (pos.entry_price - ltp) if is_short else (ltp - pos.entry_price)
                pos.unrealized_pnl = diff * pos.quantity
        responses.append(_to_response(pos, signal))

    return responses


@router.get("/{position_id}", response_model=PositionResponse)
async def get_position(position_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Position).where(Position.id == position_id))
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.post("/{position_id}/close", response_model=PositionResponse)
async def close_position(
    position_id: uuid.UUID,
    body: PositionCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    # Close the corresponding trade
    trade_result = await db.execute(select(Trade).where(Trade.id == position.trade_id))
    trade = trade_result.scalar_one_or_none()
    if trade:
        trade.status = TradeStatus.CLOSED
        trade.exit_reason = body.reason
        trade.exit_time = now_ist()
        if position.current_price:
            trade.exit_price = position.current_price
            is_short = trade.side == "SELL"
            diff = (trade.entry_price - position.current_price) if is_short else (position.current_price - trade.entry_price)
            trade.pnl = diff * trade.quantity
            trade.pnl_percent = float(diff / trade.entry_price * 100)

    pnl = float(trade.pnl) if trade and trade.pnl is not None else 0.0
    exit_price = float(trade.exit_price) if trade and trade.exit_price is not None else 0.0

    # Delete position
    await db.delete(position)
    await db.flush()

    await ws_manager.broadcast(
        "position:closed",
        {
            "position_id": str(position_id),
            "trade_id": str(position.trade_id),
            "exit_price": exit_price,
            "pnl": pnl,
        },
    )
    return position


@router.patch("/{position_id}/sl", response_model=PositionResponse)
async def update_stop_loss(
    position_id: uuid.UUID,
    body: PositionUpdateSLRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    position.stop_loss = body.stop_loss

    # Also update the trade's SL
    trade_result = await db.execute(select(Trade).where(Trade.id == position.trade_id))
    trade = trade_result.scalar_one_or_none()
    if trade:
        trade.stop_loss = body.stop_loss

    await db.flush()
    return position
