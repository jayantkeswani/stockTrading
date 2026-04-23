import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import LOT_SIZES
from app.services.position_sizing import calculate_lots
from app.services.trading_config import get_trading_config
from app.core.database import get_db
from app.core.enums import AgentActionType, SignalStatus, TradeStatus
from app.core.redis import get_cached_price
from app.core.utils import now_ist
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.schemas.signal import SignalResponse
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


@router.post("/{signal_id}/execute")
async def execute_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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

    # Determine lot size and quantity
    is_futures = signal.instrument_type == "FUTURE"
    if is_futures:
        lot_size = (signal.indicators or {}).get("futures_lot_size", 1)
    else:
        lot_size = LOT_SIZES.get(signal.symbol, 75)

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
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target_price=signal.target_price,
        status=TradeStatus.OPEN.value,
        position_type=position_type,
        is_paper=cfg.paper_trading,
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
        entry_price=signal.entry_price,
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

    # Fetch current market price for the trading symbol so the frontend
    # can show correct P&L immediately (before the first live tick arrives).
    current_price = float(signal.entry_price)
    unrealized_pnl = 0.0
    if trading_symbol:
        try:
            cached = await get_cached_price(trading_symbol)
            if cached and cached.get("ltp"):
                ltp = float(cached["ltp"])
                current_price = ltp
                unrealized_pnl = (ltp - float(signal.entry_price)) * quantity
        except Exception:
            pass

    # Broadcast trade:open with full position data for frontend
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
            "entry_price": float(signal.entry_price),
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
            "stop_loss": float(signal.stop_loss),
            "target_price": float(signal.target_price) if signal.target_price else None,
            "strategy_name": signal.strategy_name,
            "is_paper": cfg.paper_trading,
            "position_type": position_type,
            "opened_at": now.isoformat(),
        },
    )

    logger.info(
        "Manual execute: %s %s %s @ %s (%d lots)",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        signal.entry_price,
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
