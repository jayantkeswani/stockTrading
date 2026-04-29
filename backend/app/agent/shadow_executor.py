"""Shadow executor — auto-executes every signal for signal accuracy measurement.

Unlike the YOLO auto-executor this path has zero gating:
- No `executable` check (fires on blocked signals too).
- No open-position dedup (one shadow trade per signal regardless of existing shadows).
- No `_final_risk_check` (risk limits must never be polluted by shadow data).

Trades are tagged source="SHADOW" and positions tagged is_shadow=True so they are
invisible to all real-trading queries, risk calculations, and the default Trades page.
The trade_monitor loop already iterates all positions, so shadow positions get live
price updates, SL/target hits, and EOD exits for free — no separate monitor needed.
"""

import logging

from app.core.constants import LOT_SIZES
from app.core.database import async_session_factory
from app.core.enums import AgentActionType, SignalStatus, TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.live_price import get_live_price
from app.services.position_sizing import calculate_lots
from app.services.trading_config import get_trading_config
from app.websocket.manager import ws_manager
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def shadow_execute_signal(signal_id) -> None:
    """Create a shadow Trade + Position for every generated signal.

    Never raises — all errors are caught and logged so the caller's
    fire-and-forget create_task always succeeds.
    """
    try:
        await _do_shadow_execute(signal_id)
    except Exception:
        logger.exception("Shadow executor failed for signal %s", signal_id)


async def _do_shadow_execute(signal_id) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()
        if signal is None:
            logger.warning("Shadow execute: signal %s not found", signal_id)
            return

        if signal.status != SignalStatus.PENDING.value:
            logger.debug("Shadow execute: signal %s is %s, skipping", signal_id, signal.status)
            return

        # Confidence gate — skip shadow trades for low-confidence signals
        from app.services.strategy_params import get_strategy_params
        params = await get_strategy_params(signal.strategy_name)
        min_shadow_conf = params.get("min_confidence_for_shadow")
        if min_shadow_conf is not None and signal.confidence is not None:
            if float(signal.confidence) < min_shadow_conf:
                logger.debug(
                    "Shadow skip: confidence %.0f < shadow threshold %.0f for %s",
                    float(signal.confidence), min_shadow_conf, signal.symbol,
                )
                return

        is_futures = signal.instrument_type == "FUTURE"
        if is_futures:
            lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
        else:
            lot_size = LOT_SIZES.get(signal.symbol, 75)

        cfg = await get_trading_config()
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

        if is_futures:
            option_type = None
            from app.core.enums import StrategyName
            from app.strategies.registry import get_strategy
            try:
                strat = get_strategy(StrategyName(signal.strategy_name))
                position_type = getattr(strat, "holding_type", "INTRADAY") if strat else "INTRADAY"
            except (ValueError, KeyError):
                position_type = "INTRADAY"
        else:
            option_type = signal.signal_type.replace("BUY_", "")
            position_type = "INTRADAY"

        trading_symbol = signal.fyers_futures_symbol or signal.fyers_option_symbol

        if trading_symbol:
            try:
                entry_price = await get_live_price(trading_symbol)
            except Exception:
                entry_price = float(signal.entry_price)
        else:
            entry_price = float(signal.entry_price)

        now = now_ist()

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
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            status=TradeStatus.OPEN.value,
            position_type=position_type,
            is_paper=True,
            source=TradeSource.SHADOW.value,
            entry_time=now,
            fyers_option_symbol=trading_symbol,
        )
        session.add(trade)
        await session.flush()

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
            is_paper=True,
            is_shadow=True,
            opened_at=now,
        )
        session.add(position)

        log = AgentLog(
            action_type=AgentActionType.SHADOW_EXECUTED.value,
            trade_id=trade.id,
            details={
                "signal_id": str(signal.id),
                "symbol": signal.symbol,
                "strategy_name": signal.strategy_name,
                "signal_type": signal.signal_type,
                "strike_price": float(signal.strike_price),
                "entry_price": entry_price,
                "stop_loss": float(signal.stop_loss),
                "target_price": float(signal.target_price) if signal.target_price else None,
                "lots": lots,
                "executable": signal.executable,
                "blocked_reason": signal.blocked_reason,
            },
            requires_confirmation=False,
        )
        session.add(log)
        await session.commit()

    # Subscribe trading symbol for live price tracking (idempotent)
    if trading_symbol:
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client
            await fyers_ws_client.subscribe_symbols([trading_symbol])
        except Exception:
            logger.warning("Could not subscribe to %s on websocket", trading_symbol)

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
            "is_paper": True,
            "is_shadow": True,
            "source": TradeSource.SHADOW.value,
            "position_type": position_type,
            "opened_at": now.isoformat(),
        },
    )

    logger.info(
        "Shadow executed: %s %s %s @ %.2f (%d lots) executable=%s",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        entry_price,
        lots,
        signal.executable,
    )
