"""Trade monitor — watches open positions and enforces SL/target.

Supports multiple autonomy levels:
- MANUAL: Signals shown, user executes (monitor only broadcasts updates)
- SEMI: Auto-close on SL hit, request confirmation for profit booking
- YOLO: Auto-close SL, auto-book profits, auto-close at EOD
"""

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AgentActionType, ConfirmationStatus, ExitReason, TradeStatus
from app.core.redis import get_cached_price
from app.core.utils import is_past_close_deadline, now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.models.trade import Trade
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


async def monitor_positions(db: AsyncSession, yolo_mode: bool = False) -> list[dict]:
    """Check all open positions against SL, target, and time limits.

    Args:
        db: Async database session.
        yolo_mode: If True, auto-book profits without confirmation.

    Returns list of actions taken.
    """
    result = await db.execute(select(Position))
    positions = result.scalars().all()
    actions = []

    for pos in positions:
        action = await _check_position(db, pos, yolo_mode=yolo_mode)
        if action:
            actions.append(action)

    return actions


async def _check_position(
    db: AsyncSession,
    pos: Position,
    yolo_mode: bool = False,
) -> dict | None:
    """Check a single position for exit conditions."""

    # Get current option price from Redis (use option symbol if available, else index)
    price_symbol = pos.fyers_option_symbol or pos.symbol
    price_data = await get_cached_price(price_symbol)
    if not price_data:
        # Fallback: try Fyers REST for option premium
        if pos.fyers_option_symbol:
            price_data = await _fetch_option_price_rest(pos.fyers_option_symbol)
        if not price_data:
            return None

    current_price = Decimal(str(price_data.get("ltp", 0)))
    if current_price <= 0:
        return None

    # Update position's current price and unrealized P&L
    pos.current_price = current_price
    pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

    # Broadcast position update
    await ws_manager.broadcast("position:update", {
        "position_id": str(pos.id),
        "current_price": float(current_price),
        "unrealized_pnl": float(pos.unrealized_pnl),
        "pnl_percent": float(
            (current_price - pos.entry_price) / pos.entry_price * 100
        ),
    })

    # 1. Check SL — AUTO CLOSE (no confirmation needed in SEMI or YOLO)
    if current_price <= pos.stop_loss:
        return await _close_position(
            db, pos, current_price, ExitReason.AGENT_SL,
            AgentActionType.SL_TRIGGERED, requires_confirmation=False,
        )

    # 2. Check target
    if pos.target_price and current_price >= pos.target_price:
        if yolo_mode:
            # YOLO: auto-book profit, no confirmation
            return await _close_position(
                db, pos, current_price, ExitReason.AGENT_PROFIT,
                AgentActionType.AUTO_PROFIT_BOOKED, requires_confirmation=False,
            )
        else:
            # SEMI: request confirmation from user
            return await _request_profit_confirmation(db, pos, current_price)

    # 3. Trailing stop for POSITIONAL positions — move SL to breakeven after 10% gain
    if getattr(pos, "position_type", "INTRADAY") == "POSITIONAL":
        from app.core.constants import CANSLIM_TRAILING_SL_ACTIVATION_PCT
        gain_pct = float((current_price - pos.entry_price) / pos.entry_price * 100)
        if gain_pct >= CANSLIM_TRAILING_SL_ACTIVATION_PCT and pos.stop_loss < pos.entry_price:
            pos.stop_loss = pos.entry_price
            logger.info(
                "Trailing stop activated for %s: SL moved to breakeven %.2f",
                pos.symbol, float(pos.entry_price),
            )

    # 4. Expiry check for POSITIONAL positions — alert 3 days before futures expiry
    if getattr(pos, "position_type", "INTRADAY") == "POSITIONAL" and pos.expiry_date:
        from app.core.constants import FUTURES_EXPIRY_ROLL_DAYS
        days_to_expiry = (pos.expiry_date - now_ist().date()).days
        if days_to_expiry <= FUTURES_EXPIRY_ROLL_DAYS:
            if yolo_mode:
                return await _close_position(
                    db, pos, current_price, ExitReason.EXPIRY_ROLL,
                    AgentActionType.TIME_EXIT, requires_confirmation=False,
                )
            else:
                # SEMI: alert user to roll or close
                return await _request_profit_confirmation(db, pos, current_price)

    # 5. Check time — AUTO CLOSE at 3:15 PM (INTRADAY only)
    if getattr(pos, "position_type", "INTRADAY") == "INTRADAY" and is_past_close_deadline():
        return await _close_position(
            db, pos, current_price, ExitReason.TIME_EXIT,
            AgentActionType.TIME_EXIT, requires_confirmation=False,
        )

    return None


async def _close_position(
    db: AsyncSession,
    pos: Position,
    exit_price: Decimal,
    exit_reason: ExitReason,
    action_type: AgentActionType,
    requires_confirmation: bool = False,
) -> dict:
    """Close a position and update the corresponding trade."""

    # Update trade
    trade_result = await db.execute(select(Trade).where(Trade.id == pos.trade_id))
    trade = trade_result.scalar_one_or_none()
    if trade:
        trade.status = TradeStatus.CLOSED
        trade.exit_price = exit_price
        trade.exit_time = now_ist()
        trade.exit_reason = exit_reason.value
        trade.pnl = (exit_price - trade.entry_price) * trade.quantity
        trade.pnl_percent = (exit_price - trade.entry_price) / trade.entry_price * 100

    # Log agent action
    log = AgentLog(
        action_type=action_type.value,
        trade_id=pos.trade_id,
        details={
            "symbol": pos.symbol,
            "strategy_name": pos.strategy_name,
            "entry_price": float(pos.entry_price),
            "exit_price": float(exit_price),
            "pnl": float(trade.pnl) if trade and trade.pnl else 0,
            "reason": exit_reason.value,
        },
        requires_confirmation=requires_confirmation,
        confirmation_status=ConfirmationStatus.PENDING if requires_confirmation else None,
    )
    db.add(log)

    # Delete position
    await db.delete(pos)
    await db.flush()

    # Broadcast
    await ws_manager.broadcast("position:closed", {
        "position_id": str(pos.id),
        "exit_price": float(exit_price),
        "pnl": float(trade.pnl) if trade and trade.pnl else 0,
        "exit_reason": exit_reason.value,
    })

    action = {
        "action": action_type.value,
        "symbol": pos.symbol,
        "pnl": float(trade.pnl) if trade and trade.pnl else 0,
    }
    logger.info("Position closed: %s", action)
    return action


async def _request_profit_confirmation(
    db: AsyncSession, pos: Position, current_price: Decimal
) -> dict:
    """Request user confirmation for profit booking."""

    log = AgentLog(
        action_type=AgentActionType.PROFIT_BOOK_REQUEST.value,
        trade_id=pos.trade_id,
        details={
            "symbol": pos.symbol,
            "strategy_name": pos.strategy_name,
            "entry_price": float(pos.entry_price),
            "current_price": float(current_price),
            "unrealized_pnl": float(pos.unrealized_pnl) if pos.unrealized_pnl else 0,
            "target_price": float(pos.target_price) if pos.target_price else 0,
            "message": f"Target reached for {pos.symbol}. Book profit?",
        },
        requires_confirmation=True,
        confirmation_status=ConfirmationStatus.PENDING,
    )
    db.add(log)
    await db.flush()

    # Broadcast confirmation request to UI
    await ws_manager.broadcast("agent:confirmation_request", {
        "log_id": str(log.id),
        "action_type": AgentActionType.PROFIT_BOOK_REQUEST.value,
        "trade_id": str(pos.trade_id),
        "message": f"Target reached for {pos.symbol} at ₹{current_price}. Book profit?",
        "suggested_action": "CLOSE",
    })

    return {
        "action": AgentActionType.PROFIT_BOOK_REQUEST.value,
        "symbol": pos.symbol,
        "log_id": str(log.id),
    }


async def _fetch_option_price_rest(fyers_symbol: str) -> dict | None:
    """Fallback: fetch option LTP via Fyers REST when not in Redis cache."""
    try:
        from app.services.option_resolver import fetch_option_premium

        premium = await fetch_option_premium(fyers_symbol)
        if premium and premium > 0:
            return {"ltp": premium}
    except Exception:
        logger.exception("Failed REST fallback for %s", fyers_symbol)
    return None
