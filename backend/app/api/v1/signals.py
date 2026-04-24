import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import LOT_SIZES
from app.services.position_sizing import calculate_lots
from app.services.trading_config import get_trading_config
from app.services.live_price import get_live_price
from app.core.database import get_db
from app.core.enums import AgentActionType, SignalStatus, TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.signal import ExecuteSignalRequest, SignalPreviewResponse, SignalResponse
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[SignalResponse])
async def list_signals(
    status: str | None = None,
    strategy: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Signal).order_by(desc(Signal.generated_at))
    if status:
        query = query.where(Signal.status == status)
    if strategy:
        query = query.where(Signal.strategy_name == strategy)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/active", response_model=list[SignalResponse])
async def active_signals(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal)
        .where(Signal.status == SignalStatus.PENDING)
        .order_by(desc(Signal.generated_at))
    )
    return result.scalars().all()


@router.get("/{signal_id}/preview", response_model=SignalPreviewResponse)
async def preview_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return sizing preview for a signal including live current entry price."""
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status != SignalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Signal is not pending")

    is_futures = signal.instrument_type == "FUTURE"
    if is_futures:
        lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
    else:
        lot_size = LOT_SIZES.get(signal.symbol, 75)

    # Use snapshotted lots if available, else compute fresh
    if signal.lots is not None:
        lots = signal.lots
    else:
        cfg = await get_trading_config()
        lots = calculate_lots(
            capital=cfg.capital,
            risk_per_trade_pct=cfg.max_risk_per_trade_pct,
            entry_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            lot_size=lot_size,
        )

    quantity = lots * lot_size
    trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol

    # Fetch live entry price
    if trading_symbol:
        live_entry = await get_live_price(trading_symbol)
    else:
        live_entry = float(signal.entry_price)

    sl = float(signal.stop_loss)
    target = float(signal.target_price) if signal.target_price else None
    capital_at_risk = abs(live_entry - sl) * quantity

    return SignalPreviewResponse(
        signal_id=signal.id,
        lots=lots,
        quantity=quantity,
        lot_size=lot_size,
        entry_price=live_entry,
        stop_loss=sl,
        target_price=target,
        capital_at_risk=capital_at_risk,
        sizing_meta=signal.sizing_meta,
    )


@router.post("/{signal_id}/execute")
async def execute_signal(
    signal_id: uuid.UUID,
    body: ExecuteSignalRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status != SignalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Signal is not pending")

    # Check for existing open position on the same symbol + direction
    direction = signal.signal_type.replace("BUY_", "") if signal.instrument_type == "OPTION" else None
    pos_query = select(Position).where(Position.symbol == signal.symbol)
    if direction:
        pos_query = pos_query.where(Position.option_type == direction)
    existing_pos = await db.execute(pos_query)
    if existing_pos.scalar_one_or_none():
        label = f"{signal.symbol} {direction}" if direction else f"{signal.symbol} FUT"
        raise HTTPException(
            status_code=409,
            detail=f"Open position already exists for {label}",
        )

    # Determine lot size
    is_futures = signal.instrument_type == "FUTURE"
    if is_futures:
        lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
    else:
        lot_size = LOT_SIZES.get(signal.symbol, 75)

    # Use user-supplied lots override → snapshotted lots → fallback compute
    if body and body.lots is not None:
        lots = max(1, body.lots)
    elif signal.lots is not None:
        lots = signal.lots
    else:
        cfg = await get_trading_config()
        lots = calculate_lots(
            capital=cfg.capital,
            risk_per_trade_pct=cfg.max_risk_per_trade_pct,
            entry_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            lot_size=lot_size,
        )

    quantity = lots * lot_size
    now = now_ist()

    # Derive option_type and position_type
    if is_futures:
        option_type = None
        from app.strategies.registry import get_strategy
        from app.core.enums import StrategyName
        try:
            strat = get_strategy(StrategyName(signal.strategy_name))
            position_type = getattr(strat, "holding_type", "INTRADAY") if strat else "INTRADAY"
        except (ValueError, KeyError):
            position_type = "INTRADAY"
    else:
        option_type = signal.signal_type.replace("BUY_", "")
        position_type = "INTRADAY"

    trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol

    # Use live price as entry — never the stale signal premium
    if trading_symbol:
        entry_price = await get_live_price(trading_symbol)
    else:
        entry_price = float(signal.entry_price)

    cfg = await get_trading_config()

    # Create Trade
    trade = Trade(
        signal_id=signal.id,
        strategy_name=signal.strategy_name,
        symbol=signal.symbol,
        expiry_date=signal.expiry_date,
        strike_price=signal.strike_price,
        option_type=option_type,
        side="BUY",
        quantity=quantity,
        lots=lots,
        entry_price=entry_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        status=TradeStatus.OPEN.value,
        position_type=position_type,
        is_paper=cfg.paper_trading,
        source=TradeSource.MANUAL.value,
        entry_time=now,
        fyers_option_symbol=trading_symbol,
    )
    db.add(trade)
    await db.flush()

    # Create Position
    position = Position(
        trade_id=trade.id,
        symbol=signal.symbol,
        strike_price=signal.strike_price,
        option_type=option_type or "",
        expiry_date=signal.expiry_date,
        lots=lots,
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        fyers_option_symbol=trading_symbol,
        strategy_name=signal.strategy_name,
        position_type=position_type,
        is_paper=cfg.paper_trading,
        opened_at=now,
    )
    db.add(position)
    await db.flush()

    # Mark signal as executed
    signal.status = SignalStatus.EXECUTED.value
    signal.executed_trade_id = trade.id

    await db.flush()

    # Subscribe to trading symbol for live price tracking
    if trading_symbol:
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client
            await fyers_ws_client.subscribe_symbols([trading_symbol])
        except Exception:
            logger.warning("Could not subscribe to %s on websocket", trading_symbol)

    # Broadcast trade:open with live entry price
    await ws_manager.broadcast(
        "trade:open",
        {
            "id": str(position.id),
            "trade_id": str(trade.id),
            "symbol": signal.symbol,
            "fyers_option_symbol": trading_symbol,
            "strike_price": float(signal.strike_price),
            "option_type": option_type or "",
            "expiry_date": str(signal.expiry_date),
            "lots": lots,
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": entry_price,
            "unrealized_pnl": 0.0,
            "stop_loss": float(signal.stop_loss),
            "target_price": float(signal.target_price) if signal.target_price else None,
            "strategy_name": signal.strategy_name,
            "is_paper": cfg.paper_trading,
            "position_type": position_type,
            "opened_at": now.isoformat(),
        },
    )

    logger.info(
        "Manual execute: %s %s %s @ %.2f (%d lots)",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        entry_price,
        lots,
    )

    return {
        "status": "executed",
        "signal_id": str(signal_id),
        "trade_id": str(trade.id),
        "position_id": str(position.id),
    }


@router.post("/{signal_id}/reject")
async def reject_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.status = SignalStatus.REJECTED
    await db.flush()
    return {"status": "rejected", "signal_id": str(signal_id)}
