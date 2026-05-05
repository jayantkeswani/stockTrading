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

from app.agent.notification import notify_auto_executed, notify_drawdown_halt
from app.core.constants import (
    IST,
    LOT_SIZES,
    MARKET_OPEN,
)
from app.services.position_sizing import calculate_lots
from app.services.live_price import get_live_price
from app.services.trading_config import get_trading_config
from app.core.database import async_session_factory
from app.core.enums import AgentActionType, SignalStatus, TradeSource, TradeStatus
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

        # Confidence gate — only execute signals above the strategy's execution threshold
        from app.services.strategy_params import get_strategy_params
        params = await get_strategy_params(signal.strategy_name)
        min_exec_conf = params.get("min_confidence_for_execution")
        if min_exec_conf is not None and signal.confidence is not None:
            if float(signal.confidence) < min_exec_conf:
                logger.info(
                    "Auto-execute: confidence %.0f < execution threshold %.0f for %s, skipping",
                    float(signal.confidence), min_exec_conf, signal.symbol,
                )
                return None

        # Check for existing open position on the same symbol + direction
        direction = signal.signal_type.replace("BUY_", "") if signal.instrument_type == "OPTION" else None
        pos_query = select(Position).where(Position.symbol == signal.symbol)
        if direction:
            pos_query = pos_query.where(Position.option_type == direction)
        existing_pos = (await session.execute(pos_query)).scalar_one_or_none()
        if existing_pos:
            logger.info("Auto-execute: open position already exists for %s %s, skipping", signal.symbol, direction or "FUT")
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
        is_futures = signal.instrument_type == "FUTURE"
        if is_futures:
            lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
        else:
            lot_size = LOT_SIZES.get(signal.symbol, 75)

        cfg = await get_trading_config()
        # Use snapshotted lots (frozen at signal generation) or fall back to compute
        if signal.lots is not None:
            lots = signal.lots
        else:
            lots = calculate_lots(
                capital=cfg.capital,
                risk_per_trade_pct=cfg.max_risk_per_trade_pct,
                entry_price=float(signal.entry_price),
                stop_loss=float(signal.stop_loss),
                lot_size=lot_size,
            )
        quantity = lots * lot_size

        now = now_ist()

        # Derive option_type and position_type based on instrument
        if is_futures:
            option_type = None
            # Determine holding type from strategy
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

        # Use futures symbol if available, otherwise option symbol
        trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol

        # Fetch live price — YOLO executes at current market price, not stale premium
        if trading_symbol:
            try:
                live_entry = await get_live_price(trading_symbol)
            except Exception:
                logger.warning(
                    "Live price unavailable for %s, falling back to signal premium", trading_symbol
                )
                live_entry = float(signal.entry_price)
        else:
            live_entry = float(signal.entry_price)

        # Create Trade
        trade = Trade(
            signal_id=signal.id,
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            expiry_date=signal.expiry_date,
            strike_price=signal.strike_price,
            option_type=option_type,
            side="SELL" if "SELL" in signal.signal_type else "BUY",
            quantity=quantity,
            lots=lots,
            entry_price=live_entry,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            status=TradeStatus.OPEN.value,
            position_type=position_type,
            is_paper=cfg.paper_trading,
            source=TradeSource.YOLO.value,
            entry_time=now,
            fyers_option_symbol=trading_symbol,
        )
        session.add(trade)
        await session.flush()  # Get trade.id

        # Create Position
        position = Position(
            trade_id=trade.id,
            symbol=signal.symbol,
            strike_price=signal.strike_price,
            option_type=option_type or "",
            expiry_date=signal.expiry_date,
            lots=lots,
            quantity=quantity,
            entry_price=live_entry,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            fyers_option_symbol=trading_symbol,
            strategy_name=signal.strategy_name,
            position_type=position_type,
            is_paper=cfg.paper_trading,
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
                "strategy_name": signal.strategy_name,
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

    # Subscribe to trading symbol on websocket feed for live price tracking
    trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol
    if trading_symbol:
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client

            await fyers_ws_client.subscribe_symbols([trading_symbol])
        except Exception:
            logger.warning("Could not subscribe to %s on websocket", trading_symbol)

    # Broadcast trade:open so the position appears in the frontend immediately
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
            "entry_price": live_entry,
            "current_price": live_entry,
            "unrealized_pnl": 0.0,
            "stop_loss": float(signal.stop_loss),
            "target_price": float(signal.target_price) if signal.target_price else None,
            "strategy_name": signal.strategy_name,
            "is_paper": cfg.paper_trading,
            "position_type": position_type,
            "opened_at": now.isoformat(),
        },
    )

    action = {
        "action_type": AgentActionType.AUTO_EXECUTED.value,
        "signal_id": str(signal.id),
        "trade_id": str(trade.id),
        "symbol": signal.symbol,
        "signal_type": signal.signal_type,
        "strike_price": float(signal.strike_price),
        "entry_price": live_entry,
        "lots": lots,
    }
    await ws_manager.broadcast("agent:auto_executed", action)

    await notify_auto_executed(
        symbol=signal.symbol,
        signal_type=signal.signal_type,
        strategy_name=signal.strategy_name,
        entry=live_entry,
        stop_loss=float(signal.stop_loss),
        target=float(signal.target_price) if signal.target_price else 0,
        strike=float(signal.strike_price) if signal.strike_price else None,
        expiry=str(signal.expiry_date) if signal.expiry_date else None,
        lots=lots,
        quantity=quantity,
        instrument_type=signal.instrument_type or "OPTION",
    )

    logger.info(
        "YOLO auto-executed: %s %s %s @ %.2f (%d lots)",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        live_entry,
        lots,
    )
    return action


async def _final_risk_check(session: AsyncSession, symbol: str) -> tuple[bool, str | None]:
    """One final risk validation before auto-execution.

    Returns (is_safe, reason_if_not_safe).
    """
    today = now_ist().date()
    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    # Check max trades for the day — POSITIONAL and SHADOW trades are excluded.
    trade_count_result = await session.execute(
        select(func.count(Trade.id)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.symbol == symbol,
                Trade.position_type != "POSITIONAL",
                Trade.source != TradeSource.SHADOW.value,
            )
        )
    )
    trade_count = trade_count_result.scalar() or 0
    cfg = await get_trading_config()
    if trade_count >= cfg.max_trades_per_day:
        return False, f"Max trades reached ({cfg.max_trades_per_day}/day)"

    # Check drawdown — shadow P&L must never affect real-money drawdown gate.
    pnl_result = await session.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.status == TradeStatus.CLOSED.value,
                Trade.source != TradeSource.SHADOW.value,
            )
        )
    )
    realized_pnl = float(pnl_result.scalar_one())
    max_dd_amount = cfg.max_drawdown_amount

    if realized_pnl < 0 and abs(realized_pnl) >= max_dd_amount:
        try:
            await notify_drawdown_halt(daily_pnl=realized_pnl, limit=max_dd_amount)
        except Exception:
            pass
        return False, "Drawdown limit breached"

    return True, None


