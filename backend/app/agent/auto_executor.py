"""Auto-executor for YOLO mode — converts executable signals into trades automatically.

When YOLO mode is enabled, this module:
1. Picks up new executable signals (via direct call from the agent runner)
2. Validates risk limits one final time before execution
3. Creates a Trade + Position per active uncapped YOLO profile
4. Marks the signal as EXECUTED
5. Sends a Telegram notification
"""

import logging
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.notification import notify_auto_executed, notify_drawdown_halt
from app.core.constants import (
    IST,
    LOT_SIZES,
    MARKET_OPEN,
)
from app.services.execution_utils import recompute_sl_target
from app.services.live_price import get_live_price
from app.services.lot_sizing import compute_lots_for_yolo
from app.services.margin_calculator import compute_margin
from app.services.trading_config import get_trading_config
from app.services.yolo_profile_service import (
    effective_execution_threshold,
    get_active_profiles,
    get_uncapped_profile_ids,
    profile_accepts_signal,
)
from app.core.database import async_session_factory
from app.core.enums import AgentActionType, SignalStatus, TradeSource, TradeStatus
from app.core.utils import now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.models.signal import Signal
from app.models.strategy_config import StrategyConfig
from app.models.trade import Trade, build_signal_snapshot
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


async def auto_execute_signal(signal_id) -> list[dict]:
    """Attempt to auto-execute a signal in YOLO mode across all uncapped profiles.

    Each active uncapped YOLO profile gets its own Trade + Position. Shared
    computation (price, lots, SL/target, margin) happens once; per-profile
    gates (position dedup, risk check) are evaluated independently.

    Returns list of action dicts (one per profile that executed), empty list if skipped.
    """
    async with async_session_factory() as session:
        # ── Signal-level gates (shared across all profiles) ──────────

        result = await session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        signal = result.scalar_one_or_none()
        if signal is None:
            logger.warning("Auto-execute: signal %s not found", signal_id)
            return []

        if signal.status != SignalStatus.PENDING.value:
            logger.debug("Auto-execute: signal %s status is %s, skipping", signal_id, signal.status)
            return []

        if not signal.executable:
            logger.info(
                "Auto-execute: signal %s not executable (%s), skipping",
                signal_id, signal.blocked_reason,
            )
            return []

        cfg = await get_trading_config()
        # Execution confidence is now PER YOLO PROFILE (yolo_profiles.min_confidence_for_execution,
        # NULL = inherit this global default). The signal-level early-out below uses the LOWEST
        # subscribing-profile threshold (= "executable by at least one profile"); each profile then
        # re-checks its own threshold in the per-profile loop.

        sc_result = await session.execute(
            select(StrategyConfig).where(StrategyConfig.strategy_name == signal.strategy_name)
        )
        strategy_cfg = sc_result.scalar_one_or_none()
        if strategy_cfg is not None and not strategy_cfg.yolo_enabled:
            logger.info(
                "YOLO skip: strategy %s has yolo_enabled=False for signal %s",
                signal.strategy_name, signal_id,
            )
            return []

        if signal.is_permanent_watchlist and cfg.yolo_skip_permanent_watchlist:
            logger.info(
                "YOLO skip: %s is a permanent watchlist signal (yolo_skip_permanent_watchlist=True)",
                signal.symbol,
            )
            return []

        # ── Uncapped profiles ────────────────────────────────────────

        today = now_ist().date()
        uncapped_ids = await get_uncapped_profile_ids(today)
        active_profiles = await get_active_profiles()
        profiles = [p for p in active_profiles if p.id in uncapped_ids]

        if not profiles:
            logger.info("Auto-execute: no uncapped profiles for signal %s, skipping", signal_id)
            return []

        # Signal-level early-out: skip the shared computation when no subscribing profile
        # would execute this signal at its (own or inherited) confidence threshold.
        setup_type = (signal.indicators or {}).get("setup_type")
        if signal.confidence is not None:
            exec_floor = min(
                (effective_execution_threshold(p, cfg.min_confidence_for_execution)
                 for p in profiles
                 if profile_accepts_signal(p, signal.strategy_name, setup_type)),
                default=cfg.min_confidence_for_execution,
            )
            if float(signal.confidence) < exec_floor:
                logger.info(
                    "Auto-execute: confidence %.0f < lowest profile threshold %.0f for %s, skipping",
                    float(signal.confidence), exec_floor, signal.symbol,
                )
                return []

        # ── Shared computation (once for all profiles) ───────────────

        is_futures = signal.instrument_type == "FUTURE"
        if is_futures:
            lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
        else:
            lot_size = LOT_SIZES.get(signal.symbol, 75)

        india_vix = None
        try:
            from app.core.redis import get_redis
            import json as _json
            r = get_redis()
            vix_raw = await r.get("price:INDIA VIX")
            if vix_raw:
                india_vix = float(_json.loads(vix_raw).get("ltp", 0)) or None
        except Exception:
            pass

        lots, sizing_meta = await compute_lots_for_yolo(signal, lot_size, india_vix)
        quantity = lots * lot_size
        now = now_ist()

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

        stop_loss, target_price = recompute_sl_target(
            float(signal.entry_price),
            float(signal.stop_loss),
            float(signal.target_price) if signal.target_price else None,
            live_entry,
            signal.instrument_type,
            signal.signal_type,
        )

        instrument = "FUTURE" if is_futures else "OPTION"
        margin = compute_margin(signal.symbol, live_entry, quantity, instrument)
        direction = signal.signal_type.replace("BUY_", "") if signal.instrument_type == "OPTION" else None

        # ── Per-profile Trade + Position creation ────────────────────

        actions = []
        ws_payloads = []
        first_trade_id = None

        for profile in profiles:
            # Strategy/setup execution filter — a profile only acts on signals it
            # subscribes to (empty filters = all). Lets a full and a subset profile
            # run side-by-side off one signal stream.
            if not profile_accepts_signal(profile, signal.strategy_name, setup_type):
                logger.debug(
                    "Auto-execute: profile %s does not subscribe to %s/%s, skipping",
                    profile.name, signal.strategy_name, setup_type,
                )
                continue

            # Per-profile confidence gate (its own threshold, or the inherited global default).
            if signal.confidence is not None:
                threshold = effective_execution_threshold(profile, cfg.min_confidence_for_execution)
                if float(signal.confidence) < threshold:
                    logger.debug(
                        "Auto-execute: confidence %.0f < profile %s threshold %.0f, skipping",
                        float(signal.confidence), profile.name, threshold,
                    )
                    continue

            # Position dedup scoped to this profile
            pos_query = select(Position).where(
                Position.symbol == signal.symbol,
                Position.is_shadow == False,  # noqa: E712
                Position.yolo_profile_id == profile.id,
            )
            if direction:
                pos_query = pos_query.where(Position.option_type == direction)
            existing_pos = (await session.execute(pos_query)).scalar_one_or_none()
            if existing_pos:
                logger.info(
                    "Auto-execute: open position for %s %s on profile %s, skipping",
                    signal.symbol, direction or "FUT", profile.name,
                )
                continue

            is_safe, reason = await _final_risk_check(
                session, signal.symbol, profile.id, profile.profit_cap,
            )
            if not is_safe:
                logger.info(
                    "Auto-execute: risk check failed for %s profile %s — %s",
                    signal.symbol, profile.name, reason,
                )
                continue

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
                stop_loss=stop_loss,
                target_price=target_price,
                status=TradeStatus.OPEN.value,
                position_type=position_type,
                is_paper=cfg.paper_trading,
                source=TradeSource.YOLO.value,
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
                yolo_profile_id=profile.id,
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
                entry_price=live_entry,
                stop_loss=stop_loss,
                target_price=target_price,
                fyers_option_symbol=trading_symbol,
                strategy_name=signal.strategy_name,
                position_type=position_type,
                is_paper=cfg.paper_trading,
                opened_at=now,
                signal_generated_at=signal.generated_at,
                margin_required=margin,
                yolo_profile_id=profile.id,
            )
            session.add(position)
            await session.flush()

            log = AgentLog(
                action_type=AgentActionType.AUTO_EXECUTED.value,
                trade_id=trade.id,
                details={
                    "signal_id": str(signal.id),
                    "symbol": signal.symbol,
                    "strategy_name": signal.strategy_name,
                    "signal_type": signal.signal_type,
                    "strike_price": float(signal.strike_price),
                    "entry_price": live_entry,
                    "stop_loss": stop_loss,
                    "target_price": target_price,
                    "lots": lots,
                    "quantity": quantity,
                    "mode": "YOLO",
                    "profile_id": str(profile.id),
                    "profile_name": profile.name,
                },
                requires_confirmation=False,
            )
            session.add(log)

            if first_trade_id is None:
                first_trade_id = trade.id

            ws_payloads.append({
                "position_id": str(position.id),
                "trade_id": str(trade.id),
                "profile_id": str(profile.id),
                "profile_name": profile.name,
            })

            actions.append({
                "action_type": AgentActionType.AUTO_EXECUTED.value,
                "signal_id": str(signal.id),
                "trade_id": str(trade.id),
                "symbol": signal.symbol,
                "signal_type": signal.signal_type,
                "strike_price": float(signal.strike_price),
                "entry_price": live_entry,
                "lots": lots,
                "yolo_profile_id": str(profile.id),
                "yolo_profile_name": profile.name,
            })

        if not actions:
            return []

        signal.status = SignalStatus.EXECUTED.value
        signal.executed_trade_id = first_trade_id

        await session.commit()

    # ── WS broadcasts + Telegram (after commit) ─────────────────

    for wp in ws_payloads:
        await ws_manager.broadcast(
            "trade:open",
            {
                "id": wp["position_id"],
                "trade_id": wp["trade_id"],
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
                "stop_loss": stop_loss,
                "target_price": target_price,
                "strategy_name": signal.strategy_name,
                "is_paper": cfg.paper_trading,
                "position_type": position_type,
                "opened_at": now.isoformat(),
                "margin_required": margin,
                "signal_generated_at": signal.generated_at.isoformat() if signal.generated_at else None,
                "yolo_profile_id": wp["profile_id"],
                "yolo_profile_name": wp["profile_name"],
            },
        )

    for action in actions:
        await ws_manager.broadcast("agent:auto_executed", action)

    profile_names = [wp["profile_name"] for wp in ws_payloads]
    await notify_auto_executed(
        symbol=signal.symbol,
        signal_type=signal.signal_type,
        strategy_name=signal.strategy_name,
        entry=live_entry,
        stop_loss=stop_loss,
        target=target_price if target_price is not None else 0,
        strike=float(signal.strike_price) if signal.strike_price else None,
        expiry=str(signal.expiry_date) if signal.expiry_date else None,
        lots=lots,
        quantity=quantity,
        instrument_type=signal.instrument_type or "OPTION",
    )

    logger.info(
        "YOLO auto-executed: %s %s %s @ %.2f (%d lots) for %d profiles [%s]",
        signal.signal_type,
        signal.symbol,
        signal.strike_price,
        live_entry,
        lots,
        len(actions),
        ", ".join(profile_names),
    )
    return actions


async def _final_risk_check(
    session: AsyncSession, symbol: str, profile_id, profile_cap: float,
) -> tuple[bool, str | None]:
    """One final risk validation before auto-execution, scoped to a YOLO profile."""
    today = now_ist().date()
    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    # Max trades per day for this symbol and profile
    trade_count_result = await session.execute(
        select(func.count(Trade.id)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.symbol == symbol,
                Trade.position_type != "POSITIONAL",
                Trade.yolo_profile_id == profile_id,
            )
        )
    )
    trade_count = trade_count_result.scalar() or 0
    cfg = await get_trading_config()
    if trade_count >= cfg.max_trades_per_day:
        return False, f"Max trades reached ({cfg.max_trades_per_day}/day)"

    # Drawdown check scoped to profile
    pnl_result = await session.execute(
        select(func.coalesce(func.sum(Trade.pnl), 0)).where(
            and_(
                Trade.entry_time >= today_start,
                Trade.status == TradeStatus.CLOSED.value,
                Trade.yolo_profile_id == profile_id,
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

    # Profit cap — compare against profile's cap
    if profile_cap > 0:
        unrealized_result = await session.execute(
            select(func.coalesce(func.sum(Position.unrealized_pnl), 0)).where(
                Position.yolo_profile_id == profile_id,
            )
        )
        unrealized_pnl = float(unrealized_result.scalar_one())
        total_pnl = realized_pnl + unrealized_pnl
        if total_pnl >= profile_cap:
            logger.info(
                "Profit cap blocking new trade: total PnL ₹%.0f >= profile cap ₹%.0f",
                total_pnl, profile_cap,
            )
            log = AgentLog(
                action_type=AgentActionType.PROFIT_CAP_CLOSE.value,
                details={
                    "event": "profit_cap_block",
                    "daily_pnl": round(total_pnl, 0),
                    "target": round(profile_cap, 0),
                    "realized": round(realized_pnl, 0),
                    "unrealized": round(unrealized_pnl, 0),
                    "profile_id": str(profile_id),
                },
                requires_confirmation=False,
            )
            session.add(log)
            return False, "Daily profit cap reached"

    return True, None
