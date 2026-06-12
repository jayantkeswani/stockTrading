import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IST, LOT_SIZES
from app.services.execution_utils import recompute_sl_target
from app.services.lot_sizing import compute_lots_for_manual
from app.services.margin_calculator import compute_margin
from app.services.live_price import FillResult, get_fill_price
from app.services.trading_config import get_trading_config
from app.core.database import get_db
from app.core.enums import SignalStatus, TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade, build_signal_snapshot
from app.models.signal_history import SignalHistory
from app.schemas.signal import ExecuteSignalRequest, SignalHistoryResponse, SignalPreviewResponse, SignalResponse
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[SignalResponse])
async def list_signals(
    status: str | None = None,
    strategy: str | None = None,
    generated_since: datetime | None = None,
    generated_until: datetime | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    # update_count = number of archived signal_history versions (>0 ⇒ deduped/revised)
    hist_count = (
        select(func.count(SignalHistory.id))
        .where(SignalHistory.signal_id == Signal.id)
        .correlate(Signal)
        .scalar_subquery()
    )
    query = select(Signal, hist_count.label("update_count")).order_by(desc(Signal.generated_at))
    if status:
        query = query.where(Signal.status == status)
    if strategy:
        query = query.where(Signal.strategy_name == strategy)
    if generated_since is not None:
        query = query.where(Signal.generated_at >= generated_since)
    if generated_until is not None:
        query = query.where(Signal.generated_at <= generated_until)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    responses: list[SignalResponse] = []
    for signal, update_count in result.all():
        resp = SignalResponse.model_validate(signal)
        resp.update_count = int(update_count or 0)
        responses.append(resp)
    return responses


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

    # Compute lots at execution time via lot_sizing module
    lots, _sizing_meta = await compute_lots_for_manual(signal, lot_size)

    quantity = lots * lot_size
    trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol

    # Fetch the live fill price (ask for BUY / bid for SELL under BID_ASK)
    if trading_symbol:
        entry_side = "SELL" if "SELL" in signal.signal_type else "BUY"
        live_entry = (await get_fill_price(trading_symbol, entry_side)).price
    else:
        live_entry = float(signal.entry_price)

    sl, target = recompute_sl_target(
        float(signal.entry_price),
        float(signal.stop_loss),
        float(signal.target_price) if signal.target_price else None,
        live_entry,
        signal.instrument_type,
        signal.signal_type,
    )
    risk = abs(live_entry - sl) * quantity
    notional = live_entry * quantity
    margin_required = compute_margin(signal.symbol, live_entry, quantity, signal.instrument_type)

    # Generate warnings for manual execution context
    warnings: list[str] = []
    cfg = await get_trading_config()
    today = now_ist().date()
    from datetime import datetime as _dt
    from app.core.constants import MARKET_OPEN
    today_start = _dt.combine(today, MARKET_OPEN, tzinfo=IST)
    from sqlalchemy import func as sa_func
    pnl_result = await db.execute(
        select(sa_func.coalesce(sa_func.sum(Trade.pnl), 0)).where(
            Trade.entry_time >= today_start,
            Trade.status == "CLOSED",
            Trade.source != "SHADOW",
        )
    )
    today_pnl = float(pnl_result.scalar_one())
    if today_pnl < 0:
        dd_pct = abs(today_pnl) / cfg.capital * 100
        if dd_pct > cfg.max_daily_drawdown_pct * 0.6:
            warnings.append(f"Drawdown at {dd_pct:.1f}% (limit {cfg.max_daily_drawdown_pct}%)")

    trades_count_result = await db.execute(
        select(sa_func.count(Trade.id)).where(
            Trade.entry_time >= today_start,
            Trade.source != "SHADOW",
        )
    )
    trades_today = trades_count_result.scalar() or 0
    if trades_today >= cfg.max_trades_per_day:
        warnings.append(f"{trades_today}/{cfg.max_trades_per_day} trades today (limit reached)")
    elif trades_today >= cfg.max_trades_per_day - 1:
        warnings.append(f"{trades_today}/{cfg.max_trades_per_day} trades today (approaching limit)")

    from app.core.redis import get_redis
    redis_client = get_redis()
    vix_raw = await redis_client.get("indicator:global:india_vix")
    india_vix = float(vix_raw) if vix_raw else None
    if india_vix and india_vix >= 20:
        warnings.append(f"VIX elevated at {india_vix:.1f}")

    return SignalPreviewResponse(
        signal_id=signal.id,
        lots=lots,
        quantity=quantity,
        lot_size=lot_size,
        entry_price=live_entry,
        stop_loss=sl,
        target_price=target,
        risk=risk,
        notional=notional,
        margin_required=margin_required,
        sizing_meta=_sizing_meta,
        warnings=warnings,
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

    # Use user-supplied lots override, else compute fresh at execution time
    if body and body.lots is not None:
        lots = max(1, body.lots)
    else:
        lots, _sizing_meta = await compute_lots_for_manual(signal, lot_size)

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
    entry_side = "SELL" if "SELL" in signal.signal_type else "BUY"

    # Fill at the live quote — never the stale signal premium. BUY fills at the
    # ask / SELL at the bid (per trading_config.fill_model, LTP fallback recorded).
    if trading_symbol:
        entry_fill = await get_fill_price(trading_symbol, entry_side)
    else:
        entry_fill = FillResult(
            price=float(signal.entry_price), model="LTP", side=entry_side,
            ltp=None, bid=None, ask=None, fallback_reason="signal_premium",
        )
    entry_price = entry_fill.price

    # Recompute SL/target from the live fill price so R:R is preserved
    stop_loss, target_price = recompute_sl_target(
        float(signal.entry_price),
        float(signal.stop_loss),
        float(signal.target_price) if signal.target_price else None,
        entry_price,
        signal.instrument_type,
        signal.signal_type,
    )

    cfg = await get_trading_config()

    # Compute margin
    margin = compute_margin(signal.symbol, entry_price, quantity, signal.instrument_type)

    # Create Trade
    trade = Trade(
        signal_id=signal.id,
        strategy_name=signal.strategy_name,
        symbol=signal.symbol,
        expiry_date=signal.expiry_date,
        strike_price=signal.strike_price,
        option_type=option_type,
        side=entry_side,
        quantity=quantity,
        lots=lots,
        entry_price=entry_price,
        fill_model=cfg.fill_model,
        fill_meta={"entry": entry_fill.to_record()},
        stop_loss=stop_loss,
        target_price=target_price,
        status=TradeStatus.OPEN.value,
        position_type=position_type,
        is_paper=cfg.paper_trading,
        source=TradeSource.MANUAL.value,
        entry_time=now,
        fyers_option_symbol=trading_symbol,
        margin_required=margin,
        is_permanent_watchlist=bool(signal.is_permanent_watchlist),
        signal_confidence=signal.confidence,
        signal_ai_action=signal.ai_action,
        signal_ai_summary=signal.ai_summary,
        signal_instrument_type=signal.instrument_type,
        signal_type=signal.signal_type,
        signal_snapshot=build_signal_snapshot(signal),
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
        stop_loss=stop_loss,
        target_price=target_price,
        fyers_option_symbol=trading_symbol,
        strategy_name=signal.strategy_name,
        position_type=position_type,
        is_paper=cfg.paper_trading,
        opened_at=now,
        signal_generated_at=signal.generated_at,
        margin_required=margin,
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
            "stop_loss": stop_loss,
            "target_price": target_price,
            "strategy_name": signal.strategy_name,
            "is_paper": cfg.paper_trading,
            "position_type": position_type,
            "opened_at": now.isoformat(),
            "signal_generated_at": signal.generated_at.isoformat() if signal.generated_at else None,
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

    try:
        from app.agent.notification import notify_manual_executed
        await notify_manual_executed(
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            strategy_name=signal.strategy_name,
            entry=entry_price,
            stop_loss=stop_loss,
            target=target_price if target_price is not None else 0,
            strike=float(signal.strike_price) if signal.strike_price else None,
            expiry=str(signal.expiry_date) if signal.expiry_date else None,
            lots=lots,
            quantity=quantity,
            instrument_type=signal.instrument_type or "OPTION",
        )
    except Exception:
        logger.warning("Failed to send manual execution Telegram notification for %s", signal.symbol)

    return {
        "status": "executed",
        "signal_id": str(signal_id),
        "trade_id": str(trade.id),
        "position_id": str(position.id),
    }


@router.get("/{signal_id}/history", response_model=list[SignalHistoryResponse])
async def signal_history(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return all archived versions for a signal, latest version first."""
    result = await db.execute(
        select(SignalHistory)
        .where(SignalHistory.signal_id == signal_id)
        .order_by(SignalHistory.version.desc())
    )
    return result.scalars().all()


@router.post("/{signal_id}/reject")
async def reject_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.status = SignalStatus.REJECTED
    await db.flush()
    return {"status": "rejected", "signal_id": str(signal_id)}
