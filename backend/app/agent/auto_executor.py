"""Auto-executor for YOLO mode — converts executable signals into trades automatically.

When YOLO mode is enabled, this module:
1. Picks up new executable signals (via direct call from the agent runner)
2. Validates risk limits one final time before execution
3. Creates a Trade + Position from the signal
4. Marks the signal as EXECUTED
5. Sends a Telegram notification
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.notification import send_telegram
from app.config import settings
from app.core.constants import (
    DEFAULT_MAX_DAILY_DRAWDOWN_PCT,
    DEFAULT_MAX_TRADES_PER_DAY,
    IST,
    LOT_SIZES,
    MARKET_OPEN,
)
from app.core.database import async_session_factory
from app.core.enums import AgentActionType, SignalStatus, TradeStatus
from app.core.utils import now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


async def auto_execute_signal(signal_id) -> dict | None:
    """Attempt to auto-execute a signal in YOLO mode.

    Args:
        signal_id: UUID of the signal to execute.

    Returns:
        Action dict if executed, None if skipped.
    """
    async with async_session_factory() as session:
        # Load the signal
        result = await session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        signal = result.scalar_one_or_none()
        if signal is None:
            logger.warning("Auto-execute: signal %s not found", signal_id)
            return None

        # Only execute pending, executable signals
        if signal.status != SignalStatus.PENDING.value:
            logger.debug("Auto-execute: signal %s status is %s, skipping", signal_id, signal.status)
            return None

        if not signal.executable:
            logger.info(
                "Auto-execute: signal %s not executable (%s), skipping",
                signal_id,
                signal.blocked_reason,
            )
            return None

        # Final risk check before execution
        is_safe, reason = await _final_risk_check(session, signal.symbol)
        if not is_safe:
            signal.executable = False
            signal.blocked_reason = reason
            await session.commit()
            logger.warning("Auto-execute: final risk check failed for %s — %s", signal_id, reason)
            return None

        # Determine lot size and quantity
        lot_size = LOT_SIZES.get(signal.symbol, 75)
        lots = _calculate_lots(
            capital=settings.trading_capital,
            risk_per_trade_pct=settings.max_risk_per_trade_pct,
            entry_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            lot_size=lot_size,
        )
        quantity = lots * lot_size

        now = now_ist()

        # Derive option_type from signal_type (BUY_CE -> CE, BUY_PE -> PE)
        option_type = signal.signal_type.replace("BUY_", "")

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
            is_paper=settings.paper_trading,
            entry_time=now,
            fyers_option_symbol=signal.fyers_option_symbol,
        )
        session.add(trade)
        await session.flush()  # Get trade.id

        # Create Position
        position = Position(
            trade_id=trade.id,
            symbol=signal.symbol,
            strike_price=signal.strike_price,
            option_type=option_type,
            expiry_date=signal.expiry_date,
            lots=lots,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            fyers_option_symbol=signal.fyers_option_symbol,
            strategy_name=signal.strategy_name,
            is_paper=settings.paper_trading,
            opened_at=now,
        )
        session.add(position)

        # Mark signal as executed
        signal.status = SignalStatus.EXECUTED.value
        signal.executed_trade_id = trade.id

        # Log agent action
        log = AgentLog(
            action_type=AgentActionType.AUTO_EXECUTED.value,
            trade_id=trade.id,
            details={
                "signal_id": str(signal.id),
                "symbol": signal.symbol,
                "signal_type": signal.signal_type,
                "strike_price": float(signal.strike_price),
                "entry_price": float(signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "target_price": float(signal.target_price) if signal.target_price else None,
                "lots": lots,
                "quantity": quantity,
                "mode": "YOLO",
            },
            requires_confirmation=False,
        )
        session.add(log)

        await session.commit()

    # Subscribe to option symbol on websocket feed for live price tracking
    if signal.fyers_option_symbol:
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client

            await fyers_ws_client.subscribe_symbols([signal.fyers_option_symbol])
        except Exception:
            logger.warning("Could not subscribe to %s on websocket", signal.fyers_option_symbol)

    # Broadcast trade creation
    action = {
        "action": AgentActionType.AUTO_EXECUTED.value,
        "signal_id": str(signal.id),
        "trade_id": str(trade.id),
        "symbol": signal.symbol,
        "signal_type": signal.signal_type,
        "strike_price": float(signal.strike_price),
        "entry_price": float(signal.entry_price),
        "lots": lots,
    }
    await ws_manager.broadcast("agent:auto_executed", action)

    # Telegram notification
    await send_telegram(
        f"<b>Auto-executed:</b> {signal.signal_type} {signal.symbol} "
        f"{int(signal.strike_price)} @ {signal.entry_price}"
    )

    logger.info(
        "YOLO auto-executed: %s %s %s @ %s (%d lots)",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        signal.entry_price,
        lots,
    )
    return action


async def _final_risk_check(session: AsyncSession, symbol: str) -> tuple[bool, str | None]:
    """One final risk validation before auto-execution.

    Returns (is_safe, reason_if_not_safe).
    """
    today = now_ist().date()
    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    # Check max trades for the day
    trade_count_result = await session.execute(
        select(func.count(Trade.id)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.symbol == symbol,
            )
        )
    )
    trade_count = trade_count_result.scalar() or 0
    max_trades = settings.max_trades_per_day or DEFAULT_MAX_TRADES_PER_DAY
    if trade_count >= max_trades:
        return False, f"Max trades reached ({max_trades}/day)"

    # Check drawdown
    pnl_result = await session.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.status == TradeStatus.CLOSED.value,
            )
        )
    )
    realized_pnl = float(pnl_result.scalar_one())
    max_dd_pct = settings.max_daily_drawdown_pct or DEFAULT_MAX_DAILY_DRAWDOWN_PCT
    max_dd_amount = settings.trading_capital * (max_dd_pct / 100.0)

    if realized_pnl < 0 and abs(realized_pnl) >= max_dd_amount:
        return False, "Drawdown limit breached"

    return True, None


def _calculate_lots(
    capital: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss: float,
    lot_size: int,
) -> int:
    """Calculate number of lots based on risk per trade.

    Returns at least 1 lot.
    """
    risk_amount = capital * (risk_per_trade_pct / 100.0)
    risk_per_lot = abs(entry_price - stop_loss) * lot_size

    if risk_per_lot <= 0:
        return 1

    lots = int(risk_amount / risk_per_lot)
    return max(lots, 1)
