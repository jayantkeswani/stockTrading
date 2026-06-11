"""Trade monitor — watches open positions and enforces SL/target.

Supports multiple autonomy levels:
- MANUAL: Signals shown, user executes (monitor only broadcasts updates)
- SEMI: Auto-close on SL hit, request confirmation for profit booking
- YOLO: Auto-close SL, auto-book profits, auto-close at EOD
"""

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.retry import async_retry
from app.agent.notification import (
    notify_confirmation_request,
    notify_expiry_roll,
    notify_expiry_roll_failed,
    notify_invalidation_exit,
    notify_profit_booked,
    notify_profit_cap_halt,
    notify_sl_hit,
    notify_time_exit,
)
from app.core.constants import IST, MARKET_OPEN
from app.core.enums import (
    AgentActionType,
    ConfirmationStatus,
    DayBias,
    ExitReason,
    StrategyName,
    TradeSource,
    TradeStatus,
)
from app.indicators.intraday_bias import IntradayBias
from app.core.redis import get_cached_price
from app.core.utils import is_past_close_deadline, now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.models.trade import Trade
from app.services.trading_config import get_trading_config
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


# Per-position thesis-invalidation counter: position_id -> (last_counted_candle_ts, opposing_count).
# In-memory only (resets on restart, like the strategy_runner per-candle caches). The counter
# advances at most once per NIFTY 1m candle (keyed on the bias candle timestamp), so the live
# 500ms poll cannot inflate it. Pruned in monitor_positions when positions close/vanish.
_invalidation_state: dict[uuid.UUID, tuple[str | None, int]] = {}


async def monitor_positions(db: AsyncSession, yolo_mode: bool = False) -> list[dict]:
    """Check all open positions against SL, target, and time limits.

    Args:
        db: Async database session.
        yolo_mode: If True, auto-book profits without confirmation.

    Returns list of actions taken.
    """
    actions = []

    cap_actions = await _check_profit_cap(db)
    if cap_actions:
        actions.extend(cap_actions)

    result = await db.execute(select(Position))
    positions = result.scalars().all()

    # Prune invalidation counters for positions that have closed/vanished.
    open_ids = {pos.id for pos in positions}
    for pid in [pid for pid in _invalidation_state if pid not in open_ids]:
        _invalidation_state.pop(pid, None)

    for pos in positions:
        action = await _check_position(db, pos, yolo_mode=yolo_mode)
        if action:
            actions.append(action)

    return actions


async def _check_profit_cap(db: AsyncSession) -> list[dict] | None:
    """Close open positions for any YOLO profile whose daily net profit cap is reached.

    Each profile is checked independently — only that profile's positions are
    closed when its cap is hit. Other profiles continue trading.

    Uses net P&L (after brokerage, STT, exchange, GST, SEBI, stamp duty).
    Closed trades use stored net_pnl; open positions estimate charges from LTP.

    Returns list of close actions if any cap was hit, None otherwise.
    """
    from app.services.yolo_profile_service import get_active_profiles, get_uncapped_profile_ids

    profiles = await get_active_profiles()
    if not profiles:
        return None

    default_profile_id = min(profiles, key=lambda p: p.sort_order).id

    today = now_ist().date()
    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    uncapped_ids = await get_uncapped_profile_ids(today)
    profiles = [p for p in profiles if p.id in uncapped_ids]
    if not profiles:
        return None

    all_actions = []
    capped_profiles = []

    for profile in profiles:
        # Realized net PnL for this profile today
        pnl_result = await db.execute(
            select(
                func.coalesce(func.sum(Trade.net_pnl), 0),
                func.coalesce(func.sum(Trade.pnl), 0),
            ).where(
                and_(
                    Trade.entry_time >= today_start,
                    Trade.status == TradeStatus.CLOSED.value,
                    Trade.yolo_profile_id == profile.id,
                )
            )
        )
        row = pnl_result.one()
        realized_net_pnl = float(row[0])
        realized_gross_pnl = float(row[1])
        realized_pnl = realized_net_pnl if realized_net_pnl != 0 or realized_gross_pnl == 0 else realized_gross_pnl

        unrealized_pnl = await _unrealized_net_pnl(db, profile_id=profile.id)

        total_pnl = realized_pnl + unrealized_pnl
        if total_pnl < float(profile.profit_cap):
            continue

        # Cap hit for this profile — close its open positions
        result = await db.execute(
            select(Position).where(
                Position.is_shadow == False,  # noqa: E712
                Position.yolo_profile_id == profile.id,
            )
        )
        open_positions = result.scalars().all()
        if not open_positions:
            continue

        for pos in open_positions:
            price_symbol = pos.fyers_option_symbol or pos.symbol
            price_data = await get_cached_price(price_symbol)
            if price_data:
                exit_price = Decimal(str(price_data.get("ltp", 0)))
            else:
                exit_price = pos.current_price or pos.entry_price

            action = await _close_position(
                db, pos, exit_price,
                ExitReason.PROFIT_CAP,
                AgentActionType.PROFIT_CAP_CLOSE,
            )
            all_actions.append(action)

        capped_profiles.append({
            "name": profile.name,
            "total_pnl": total_pnl,
            "cap": float(profile.profit_cap),
            "positions_closed": len(open_positions),
            "is_default": profile.id == default_profile_id,
        })

    if not all_actions:
        return None

    await db.commit()

    for cp in capped_profiles:
        # Only the default profile sends a Telegram notification (other tiers still
        # close their positions and log, just silently).
        if cp["is_default"]:
            try:
                await notify_profit_cap_halt(
                    daily_pnl=cp["total_pnl"],
                    limit=cp["cap"],
                    positions_closed=cp["positions_closed"],
                    profile_name=cp["name"],
                )
            except Exception:
                logger.warning("Failed to send profit cap notification for profile %s", cp["name"])

        logger.info(
            "Profit cap hit for profile %s: net PnL ₹%.0f >= cap ₹%.0f, closed %d positions",
            cp["name"], cp["total_pnl"], cp["cap"], cp["positions_closed"],
        )

    return all_actions


async def _unrealized_net_pnl(db: AsyncSession, profile_id=None) -> float:
    """Sum BOOKABLE unrealized P&L for open non-shadow positions, minus estimated charges.

    Valuation matches what a close would actually book under the configured fill
    model: a long exits at the cached bid, a short futures position exits at the
    cached ask (LTP fallback when the book is missing/invalid, or when
    fill_model=LTP). An LTP-valued trigger would declare the profit cap reached a
    spread too early and the booked exits would realize under the cap. Display
    MTM and SL/target triggers stay LTP-valued.

    When profile_id is given, only that YOLO profile's positions are included.
    """
    from app.services.brokerage_calculator import compute_charges
    from app.services.trading_config import get_trading_config_sync

    cfg = get_trading_config_sync()
    use_book = cfg is None or cfg.fill_model == "BID_ASK"

    query = select(Position, Trade).join(Trade, Trade.id == Position.trade_id).where(
        Position.is_shadow == False,  # noqa: E712
    )
    if profile_id is not None:
        query = query.where(Position.yolo_profile_id == profile_id)
    result = await db.execute(query)
    rows = result.all()

    total = 0.0
    for pos, trade in rows:
        is_short = trade.side == "SELL"

        price_symbol = pos.fyers_option_symbol or pos.symbol
        price_data = await get_cached_price(price_symbol)
        exit_price = None
        if price_data:
            book_side = price_data.get("ask") if is_short else price_data.get("bid")
            try:
                book_val = float(book_side) if book_side is not None else 0.0
            except (TypeError, ValueError):
                book_val = 0.0
            if use_book and book_val > 0:
                exit_price = Decimal(str(book_side))
            elif price_data.get("ltp"):
                exit_price = Decimal(str(price_data["ltp"]))
        if exit_price is None:
            exit_price = pos.current_price or pos.entry_price

        if is_short:
            gross = float((pos.entry_price - exit_price) * pos.quantity)
        else:
            gross = float((exit_price - pos.entry_price) * pos.quantity)

        instrument_type = "OPTION" if trade.option_type else "FUTURE"
        charges = compute_charges(
            instrument_type, pos.entry_price, exit_price,
            pos.quantity, trade.side,
        )
        total += gross - float(charges.total)

    return total


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
            if _is_within_grace_period(pos):
                return None
            if pos.is_shadow:
                return await _close_stale_shadow(db, pos)
            return None

    current_price = Decimal(str(price_data.get("ltp", 0)))
    if current_price <= 0:
        if _is_within_grace_period(pos):
            return None
        if pos.is_shadow:
            return await _close_stale_shadow(db, pos)
        return None

    # Update position's current price and unrealized P&L
    pos.current_price = current_price
    # Detect direction from target (immutable) — SL can be trailed past entry
    if pos.target_price is not None:
        is_short_pos = pos.target_price < pos.entry_price
    else:
        is_short_pos = pos.stop_loss > pos.entry_price

    if is_short_pos:
        pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
    else:
        pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

    pnl_pct = float(pos.unrealized_pnl / (pos.entry_price * pos.quantity) * 100) if pos.entry_price else 0.0

    # Broadcast position update
    await ws_manager.broadcast("position:update", {
        "position_id": str(pos.id),
        "current_price": float(current_price),
        "unrealized_pnl": float(pos.unrealized_pnl),
        "pnl_percent": pnl_pct,
    })

    # 1. Check SL — AUTO CLOSE (no confirmation needed in SEMI or YOLO)
    sl_hit = current_price >= pos.stop_loss if is_short_pos else current_price <= pos.stop_loss
    if sl_hit:
        trade_result = await db.execute(select(Trade).where(Trade.id == pos.trade_id))
        sl_trade = trade_result.scalar_one_or_none()
        exit_reason = (
            ExitReason.TRAILING_SL
            if sl_trade and sl_trade.stop_loss != pos.stop_loss
            else ExitReason.AGENT_SL
        )
        return await _close_position(
            db, pos, current_price, exit_reason,
            AgentActionType.SL_TRIGGERED, requires_confirmation=False,
        )

    # 2. Check target
    target_hit = (pos.target_price and current_price <= pos.target_price) if is_short_pos else (pos.target_price and current_price >= pos.target_price)
    if target_hit:
        if yolo_mode or pos.is_shadow:
            return await _close_position(
                db, pos, current_price, ExitReason.AGENT_PROFIT,
                AgentActionType.AUTO_PROFIT_BOOKED, requires_confirmation=False,
            )
        else:
            # SEMI: request confirmation from user
            return await _request_profit_confirmation(db, pos, current_price)

    # 2.5 Thesis-invalidation exit — S5/S6 momentum YOLO positions whose profile enables it.
    # Closes early when the live NIFTY bias flips STRONG-against the trade for N
    # consecutive candles. Placed after SL/target so those take precedence within a
    # candle (invalidation only ever exits earlier than the structural stops).
    inval_action = await _check_invalidation(db, pos, current_price, is_short_pos)
    if inval_action:
        return inval_action

    # 3. Trailing stop — POSITIONAL always, INTRADAY when trailing_sl_enabled
    from app.services.strategy_params import get_strategy_params_sync
    position_type = getattr(pos, "position_type", "INTRADAY")
    strat_params = get_strategy_params_sync(pos.strategy_name or "")
    should_trail = (
        position_type == "POSITIONAL"
        or strat_params.get("trailing_sl_enabled", False)
    )
    if should_trail:
        from app.core.constants import CANSLIM_TRAILING_SL_ACTIVATION_PCT
        breakeven_pct = strat_params.get(
            "trailing_sl_breakeven_pct",
            strat_params.get("trailing_sl_activation_pct", CANSLIM_TRAILING_SL_ACTIVATION_PCT),
        )

        # High water mark tracking (best price since entry)
        if is_short_pos:
            if pos.high_since_entry is None:
                pos.high_since_entry = current_price
            elif current_price < pos.high_since_entry:
                pos.high_since_entry = current_price
        else:
            if pos.high_since_entry is None:
                pos.high_since_entry = current_price
            elif current_price > pos.high_since_entry:
                pos.high_since_entry = current_price

        # Gain calculation: positive when trade moves in our favor
        if is_short_pos:
            gain_pct = float((pos.entry_price - current_price) / pos.entry_price * 100)
        else:
            gain_pct = float((current_price - pos.entry_price) / pos.entry_price * 100)

        # Breakeven activation
        if is_short_pos:
            if gain_pct >= breakeven_pct and pos.stop_loss > pos.entry_price:
                pos.stop_loss = pos.entry_price
                logger.info(
                    "Trailing stop activated for %s (SHORT): SL moved to breakeven %.2f",
                    pos.symbol, float(pos.entry_price),
                )
        else:
            if gain_pct >= breakeven_pct and pos.stop_loss < pos.entry_price:
                pos.stop_loss = pos.entry_price
                logger.info(
                    "Trailing stop activated for %s: SL moved to breakeven %.2f",
                    pos.symbol, float(pos.entry_price),
                )

        # Progressive trail — only when trail_pct is configured and SL already at breakeven+
        trail_pct = strat_params.get("trailing_sl_trail_pct")
        if trail_pct and pos.high_since_entry:
            if is_short_pos:
                if pos.stop_loss <= pos.entry_price:
                    trail_sl = pos.high_since_entry * Decimal(str(1 + trail_pct / 100))
                    if trail_sl < pos.stop_loss:
                        pos.stop_loss = trail_sl
                        logger.info(
                            "Progressive trail for %s (SHORT): SL moved to %.2f (LWM: %.2f)",
                            pos.symbol, float(trail_sl), float(pos.high_since_entry),
                        )
            else:
                if pos.stop_loss >= pos.entry_price:
                    trail_sl = pos.high_since_entry * Decimal(str(1 - trail_pct / 100))
                    if trail_sl > pos.stop_loss:
                        pos.stop_loss = trail_sl
                        logger.info(
                            "Progressive trail for %s: SL moved to %.2f (HWM: %.2f)",
                            pos.symbol, float(trail_sl), float(pos.high_since_entry),
                        )

    # 4. Expiry check for POSITIONAL positions — roll 3 days before futures expiry
    if getattr(pos, "position_type", "INTRADAY") == "POSITIONAL" and pos.expiry_date:
        from app.core.constants import FUTURES_EXPIRY_ROLL_DAYS
        days_to_expiry = (pos.expiry_date - now_ist().date()).days
        if days_to_expiry <= FUTURES_EXPIRY_ROLL_DAYS:
            if yolo_mode or pos.is_shadow:
                return await _roll_futures_position(db, pos, current_price)
            else:
                # SEMI: request confirmation before rolling
                return await _request_profit_confirmation(db, pos, current_price)

    # 5. Check time — AUTO CLOSE at 3:25 PM (INTRADAY only)
    if getattr(pos, "position_type", "INTRADAY") == "INTRADAY" and is_past_close_deadline():
        return await _close_position(
            db, pos, current_price, ExitReason.TIME_EXIT,
            AgentActionType.TIME_EXIT, requires_confirmation=False,
        )

    return None


def _bias_opposes(bias: IntradayBias, is_short: bool, strong_only: bool) -> bool:
    """Return True if the NIFTY bias points STRONG- (or MODERATE-) against the trade.

    A long (CE / BUY_FUT) is opposed by a BEARISH bias; a short by a BULLISH bias.
    `strong_only` (the profile default) requires STRONG strength; when False,
    MODERATE+ counts too (matches the backtest's --inval-moderate guard).
    """
    if strong_only:
        if bias.strength != "STRONG":
            return False
    elif bias.strength not in ("STRONG", "MODERATE"):
        return False
    return bias.bias == DayBias.BULLISH if is_short else bias.bias == DayBias.BEARISH


def _quorum_satisfied(pos: Position, current_price: Decimal, is_short: bool) -> bool:
    """Optional quorum guard: require the stock to lose/reclaim its OWN VWAP.

    A long is confirmed-invalid only when price is below the stock VWAP; a short
    only when price is above it. If the stock VWAP is unavailable, the guard fails
    closed (no exit) — conservative, since quorum is opt-in. Off by default.
    """
    from app.services.strategy_runner import strategy_runner

    try:
        vwap = strategy_runner._calculate_vwap_from_buffer(pos.symbol)
    except Exception:
        vwap = None
    vwap_val = getattr(vwap, "vwap", None) if vwap is not None else None
    if not vwap_val:
        return False
    v = Decimal(str(vwap_val))
    return current_price > v if is_short else current_price < v


async def _check_invalidation(
    db: AsyncSession, pos: Position, current_price: Decimal, is_short_pos: bool
) -> dict | None:
    """Thesis-invalidation exit for S5/S6 momentum YOLO positions whose profile enables it.

    Advances a per-position opposing-candle counter once per NIFTY 1m candle (keyed
    on the bias candle timestamp, so the 500ms poll can't inflate it). Closes the
    position with ExitReason.INVALIDATION once the count reaches the profile's
    `invalidation_persist`. Returns the close action, or None to continue monitoring.

    Gating: non-shadow INTRADAY momentum positions (S5 intraday_futures + S6
    breakout_retest) with a yolo_profile_id whose profile has invalidation_persist > 0.
    The first candle observed per position only sets a baseline (the pre-entry candle
    is never counted).
    """
    if (
        pos.is_shadow
        or (pos.strategy_name or "") not in (
            StrategyName.INTRADAY_FUTURES.value, StrategyName.BREAKOUT_RETEST.value
        )
        or getattr(pos, "position_type", "INTRADAY") != "INTRADAY"
        or pos.yolo_profile_id is None
    ):
        return None

    from app.services.yolo_profile_service import get_active_profiles_sync

    profile = next(
        (p for p in get_active_profiles_sync() if p.id == pos.yolo_profile_id), None
    )
    if profile is None or not profile.invalidation_persist or profile.invalidation_persist <= 0:
        _invalidation_state.pop(pos.id, None)
        return None

    from app.services.strategy_runner import strategy_runner

    bias, candle_ts = strategy_runner.nifty_bias_snapshot()
    if bias is None or candle_ts is None:
        return None

    last_ts, count = _invalidation_state.get(pos.id, (None, 0))
    if candle_ts == last_ts:
        # Already processed this candle — counter unchanged (poll de-dup).
        pass
    elif last_ts is None:
        # First observation for this position: baseline only. Never count the
        # in-flight/pre-entry candle.
        _invalidation_state[pos.id] = (candle_ts, 0)
        return None
    else:
        opposing = _bias_opposes(bias, is_short_pos, profile.invalidation_strong_only)
        if opposing and profile.invalidation_quorum:
            opposing = _quorum_satisfied(pos, current_price, is_short_pos)
        count = count + 1 if opposing else 0
        _invalidation_state[pos.id] = (candle_ts, count)

    if count >= profile.invalidation_persist:
        _invalidation_state.pop(pos.id, None)
        logger.info(
            "Thesis invalidation exit for %s (profile %s): NIFTY bias %s/%s opposed "
            "%s position for %d candles",
            pos.symbol, profile.name, bias.bias.value, bias.strength,
            "SHORT" if is_short_pos else "LONG", count,
        )
        return await _close_position(
            db, pos, current_price, ExitReason.INVALIDATION,
            AgentActionType.INVALIDATION_CLOSE, requires_confirmation=False,
        )
    return None


async def _close_position(
    db: AsyncSession,
    pos: Position,
    exit_price: Decimal,
    exit_reason: ExitReason,
    action_type: AgentActionType,
    requires_confirmation: bool = False,
    fill_at_market: bool = True,
) -> dict:
    """Close a position and update the corresponding trade.

    ``exit_price`` is the TRIGGER price (LTP) the caller detected the exit at.
    When ``fill_at_market`` is True (every live exit), the actual booked exit is
    re-quoted via get_fill_price — a long exit SELLs at the bid, a short-futures
    exit BUYs at the ask — falling back to the trigger LTP when no book is
    available. ``fill_at_market=False`` (stale-data closes) books exactly the
    passed price. The fill snapshot is recorded in Trade.fill_meta["exit"].
    """
    from app.services.live_price import get_fill_price

    # Update trade
    trade_result = await db.execute(select(Trade).where(Trade.id == pos.trade_id))
    trade = trade_result.scalar_one_or_none()
    if trade:
        exit_side = "BUY" if trade.side == "SELL" else "SELL"
        exit_record = {
            "model": "LTP",
            "fallback": "stale_data" if not fill_at_market else "no_quote",
            "side": exit_side,
            "price": float(exit_price),
            "ltp": float(exit_price) if fill_at_market else None,
            "bid": None, "ask": None,
            "spread_bps": None, "spread_cost": 0.0,
            "ts": now_ist().isoformat(),
        }
        if fill_at_market:
            try:
                fill = await get_fill_price(pos.fyers_option_symbol or pos.symbol, exit_side)
                exit_price = Decimal(str(fill.price))
                exit_record = fill.to_record()
            except Exception:
                logger.warning(
                    "Exit fill quote unavailable for %s — booking trigger LTP %.2f",
                    pos.symbol, float(exit_price),
                )
        trade.fill_meta = {**(trade.fill_meta or {}), "exit": exit_record}
        trade.status = TradeStatus.CLOSED
        trade.exit_price = exit_price
        trade.exit_time = now_ist()
        trade.exit_reason = exit_reason.value
        is_short = (pos.target_price is not None and pos.target_price < pos.entry_price)
        if is_short:
            trade.pnl = (trade.entry_price - exit_price) * trade.quantity
            trade.pnl_percent = (trade.entry_price - exit_price) / trade.entry_price * 100
        else:
            trade.pnl = (exit_price - trade.entry_price) * trade.quantity
            trade.pnl_percent = (exit_price - trade.entry_price) / trade.entry_price * 100

        from app.services.brokerage_calculator import compute_charges
        instrument_type = "OPTION" if trade.option_type else "FUTURE"
        charges = compute_charges(instrument_type, trade.entry_price, exit_price, trade.quantity, trade.side)
        trade.charges_json = charges.to_dict()
        trade.net_pnl = trade.pnl - charges.total

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
            "is_shadow": pos.is_shadow,
        },
        requires_confirmation=requires_confirmation,
        confirmation_status=ConfirmationStatus.PENDING if requires_confirmation else None,
    )
    db.add(log)

    # Delete position
    await db.delete(pos)
    await db.flush()
    _invalidation_state.pop(pos.id, None)

    pnl_val = float(trade.net_pnl if trade.net_pnl is not None else trade.pnl) if trade and trade.pnl else 0

    # Broadcast
    await ws_manager.broadcast("position:closed", {
        "position_id": str(pos.id),
        "exit_price": float(exit_price),
        "pnl": pnl_val,
        "exit_reason": exit_reason.value,
    })

    # Telegram alert — skip for shadow positions to avoid noise. Each YOLO signal
    # opens one position per profile; notify only for the default profile (or MANUAL
    # trades, which carry no profile) so one exit event sends one message, not N.
    if not pos.is_shadow:
        from app.services.yolo_profile_service import get_default_profile_sync
        default = get_default_profile_sync()
        # Suppress only the redundant non-default profile copies. When no default is
        # known (no profiles configured / cold cache) or the position is MANUAL, notify.
        is_notifying = (
            default is None
            or pos.yolo_profile_id is None
            or pos.yolo_profile_id == default.id
        )
        if is_notifying:
            entry = float(pos.entry_price)
            exit_f = float(exit_price)
            lots = pos.lots
            sym = pos.symbol
            strat = pos.strategy_name or ""
            inst = getattr(pos, "instrument_type", "OPTION") or "OPTION"

            if action_type == AgentActionType.SL_TRIGGERED:
                await notify_sl_hit(sym, strat, entry, exit_f, pnl_val, lots, inst, is_trailing=exit_reason == ExitReason.TRAILING_SL)
            elif action_type in (AgentActionType.AUTO_PROFIT_BOOKED, AgentActionType.PROFIT_BOOKED):
                await notify_profit_booked(sym, strat, entry, exit_f, pnl_val, lots)
            elif action_type == AgentActionType.TIME_EXIT:
                await notify_time_exit(sym, strat, entry, exit_f, pnl_val, lots)
            elif action_type == AgentActionType.INVALIDATION_CLOSE:
                await notify_invalidation_exit(sym, strat, entry, exit_f, pnl_val, lots)
            # EXPIRY_ROLL close notification is handled by _roll_futures_position

    action = {
        "id": str(log.id),
        "action_type": action_type.value,
        "trade_id": str(pos.trade_id) if pos.trade_id else None,
        "details": log.details,
        "requires_confirmation": False,
        "confirmation_status": None,
        "confirmed_at": None,
        "created_at": now_ist().isoformat(),
    }
    logger.info("Position closed: %s", action)
    return action


async def _request_profit_confirmation(
    db: AsyncSession, pos: Position, current_price: Decimal
) -> dict | None:
    """Request user confirmation for profit booking (SEMI mode).

    Returns None if a PENDING request already exists for this trade,
    preventing duplicate requests on every monitor loop iteration.
    """
    # Guard: skip if a confirmation is already pending for this trade
    existing = await db.execute(
        select(AgentLog).where(
            and_(
                AgentLog.trade_id == pos.trade_id,
                AgentLog.action_type == AgentActionType.PROFIT_BOOK_REQUEST.value,
                AgentLog.confirmation_status == ConfirmationStatus.PENDING.value,
            )
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

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
            "is_shadow": pos.is_shadow,
        },
        requires_confirmation=True,
        confirmation_status=ConfirmationStatus.PENDING,
    )
    db.add(log)
    await db.flush()

    await ws_manager.broadcast("agent:confirmation_request", {
        "log_id": str(log.id),
        "action_type": AgentActionType.PROFIT_BOOK_REQUEST.value,
        "trade_id": str(pos.trade_id),
        "message": f"Target reached for {pos.symbol} at ₹{current_price}. Book profit?",
        "suggested_action": "CLOSE",
    })

    unrealized = float(pos.unrealized_pnl) if pos.unrealized_pnl else 0
    await notify_confirmation_request(
        symbol=pos.symbol,
        strategy_name=pos.strategy_name or "",
        entry=float(pos.entry_price),
        current_price=float(current_price),
        pnl=unrealized,
    )

    return {
        "id": str(log.id),
        "action_type": AgentActionType.PROFIT_BOOK_REQUEST.value,
        "trade_id": str(pos.trade_id) if pos.trade_id else None,
        "details": log.details,
        "requires_confirmation": True,
        "confirmation_status": ConfirmationStatus.PENDING.value,
        "confirmed_at": None,
        "created_at": now_ist().isoformat(),
    }


async def _roll_futures_position(
    db: AsyncSession, pos: Position, current_price: Decimal
) -> dict:
    """Close an expiring futures position and immediately open the next month's contract.

    Preserves the same lot count and scales SL/target to the same percentage
    distances from the new entry price.
    """
    from app.config import settings
    from app.services.futures_resolver import resolve_futures_contract

    symbol = pos.symbol

    # 1. Close the current contract
    close_action = await _close_position(
        db, pos, current_price, ExitReason.EXPIRY_ROLL,
        AgentActionType.EXPIRY_ROLL, requires_confirmation=False,
    )

    # 2. Resolve the next month's contract (search from day after current expiry)
    next_from = pos.expiry_date + timedelta(days=1)
    resolution = await resolve_futures_contract(
        symbol, float(current_price), from_date=next_from
    )
    if resolution is None:
        logger.warning(
            "Expiry roll: could not resolve next contract for %s after %s — closed only",
            symbol, pos.expiry_date,
        )
        await notify_expiry_roll_failed(symbol, str(pos.expiry_date))
        return close_action

    new_ltp = Decimal(str(resolution.ltp))

    # 3. Scale SL/target to same % distances from new entry
    sl_ratio = pos.stop_loss / pos.entry_price
    new_sl = new_ltp * sl_ratio

    new_target = None
    if pos.target_price:
        target_ratio = pos.target_price / pos.entry_price
        new_target = new_ltp * target_ratio

    lots = pos.lots
    quantity = lots * resolution.lot_size
    now = now_ist()

    # Compute margin for the rolled contract
    from app.services.margin_calculator import compute_margin
    margin = compute_margin(symbol, float(new_ltp), quantity, "FUTURE")

    # 4. Create new Trade + Position
    # Roll re-entry fills at the resolution LTP (not bid/ask) — rare,
    # positional-only; recorded honestly so the spread dataset stays clean.
    trade_kwargs = dict(
        strategy_name=pos.strategy_name,
        symbol=symbol,
        expiry_date=resolution.expiry_date,
        strike_price=pos.strike_price,
        option_type=pos.option_type or None,
        side="BUY",
        quantity=quantity,
        lots=lots,
        entry_price=new_ltp,
        fill_meta={"entry": {
            "model": "LTP", "fallback": "expiry_roll", "side": "BUY",
            "price": float(new_ltp), "ltp": float(new_ltp),
            "bid": None, "ask": None, "spread_bps": None, "spread_cost": 0.0,
            "ts": now.isoformat(),
        }},
        stop_loss=new_sl,
        target_price=new_target,
        status=TradeStatus.OPEN.value,
        position_type=pos.position_type,
        is_paper=pos.is_paper,
        entry_time=now,
        fyers_option_symbol=resolution.fyers_symbol,
        margin_required=margin,
    )
    if pos.is_shadow:
        trade_kwargs["source"] = TradeSource.SHADOW.value
    if pos.yolo_profile_id:
        trade_kwargs["source"] = TradeSource.YOLO.value
        trade_kwargs["yolo_profile_id"] = pos.yolo_profile_id
    original_trade = (await db.execute(select(Trade).where(Trade.id == pos.trade_id))).scalar_one_or_none()
    # Stamp the current fill regime (cache-only read — no DB I/O on this hot
    # path); fall back to the rolled-from trade's regime when the cache is cold.
    from app.services.trading_config import get_trading_config_sync
    _cfg = get_trading_config_sync()
    trade_kwargs["fill_model"] = (
        _cfg.fill_model if _cfg else (original_trade.fill_model if original_trade else None)
    )
    if original_trade:
        trade_kwargs["is_permanent_watchlist"] = original_trade.is_permanent_watchlist
        trade_kwargs["signal_confidence"] = original_trade.signal_confidence
        trade_kwargs["signal_ai_action"] = original_trade.signal_ai_action
        trade_kwargs["signal_ai_summary"] = original_trade.signal_ai_summary
        trade_kwargs["signal_instrument_type"] = original_trade.signal_instrument_type
        trade_kwargs["signal_type"] = original_trade.signal_type
        trade_kwargs["signal_snapshot"] = original_trade.signal_snapshot
    new_trade = Trade(**trade_kwargs)
    db.add(new_trade)
    await db.flush()

    new_position = Position(
        trade_id=new_trade.id,
        symbol=symbol,
        strike_price=pos.strike_price,
        option_type=pos.option_type or "",
        expiry_date=resolution.expiry_date,
        lots=lots,
        quantity=quantity,
        entry_price=new_ltp,
        stop_loss=new_sl,
        target_price=new_target,
        fyers_option_symbol=resolution.fyers_symbol,
        strategy_name=pos.strategy_name,
        position_type=pos.position_type,
        is_paper=pos.is_paper,
        is_shadow=pos.is_shadow,
        opened_at=now,
        signal_generated_at=pos.signal_generated_at,
        margin_required=margin,
        yolo_profile_id=pos.yolo_profile_id,
    )
    db.add(new_position)

    log = AgentLog(
        action_type=AgentActionType.EXPIRY_ROLL.value,
        trade_id=new_trade.id,
        details={
            "symbol": symbol,
            "strategy_name": pos.strategy_name,
            "rolled_from_expiry": str(pos.expiry_date),
            "rolled_to_expiry": str(resolution.expiry_date),
            "old_symbol": pos.fyers_option_symbol,
            "new_symbol": resolution.fyers_symbol,
            "entry_price": float(new_ltp),
            "is_shadow": pos.is_shadow,
        },
        requires_confirmation=False,
    )
    db.add(log)
    await db.flush()

    # 5. Subscribe new symbol on WebSocket feed
    try:
        from app.data_feed.fyers_ws_client import fyers_ws_client
        await fyers_ws_client.subscribe_symbols([resolution.fyers_symbol])
    except Exception:
        logger.warning("Could not subscribe rolled symbol %s", resolution.fyers_symbol)

    # 6. Broadcast so frontend shows the new position immediately
    await ws_manager.broadcast("trade:open", {
        "id": str(new_position.id),
        "trade_id": str(new_trade.id),
        "symbol": symbol,
        "fyers_option_symbol": resolution.fyers_symbol,
        "strike_price": float(pos.strike_price),
        "option_type": pos.option_type or "",
        "expiry_date": str(resolution.expiry_date),
        "lots": lots,
        "quantity": quantity,
        "entry_price": float(new_ltp),
        "current_price": float(new_ltp),
        "unrealized_pnl": 0.0,
        "stop_loss": float(new_sl),
        "target_price": float(new_target) if new_target else None,
        "strategy_name": pos.strategy_name,
        "is_paper": pos.is_paper,
        "position_type": pos.position_type,
        "opened_at": now.isoformat(),
    })

    await notify_expiry_roll(
        symbol=symbol,
        old_expiry=str(pos.expiry_date),
        new_expiry=str(resolution.expiry_date),
        old_pnl=close_action.get("pnl", 0),
        new_entry=float(new_ltp),
        new_sl=float(new_sl),
        new_target=float(new_target) if new_target else 0,
    )

    logger.info(
        "Expiry roll: %s %s → %s @ %.2f",
        symbol, pos.expiry_date, resolution.expiry_date, float(new_ltp),
    )
    return {
        "action_type": AgentActionType.EXPIRY_ROLL.value,
        "symbol": symbol,
        "pnl": close_action.get("pnl", 0),
        "rolled_to": str(resolution.expiry_date),
        "new_symbol": resolution.fyers_symbol,
    }


_STALE_GRACE_PERIOD = timedelta(minutes=5)


def _is_within_grace_period(pos: Position) -> bool:
    """Skip stale-data closure if position was created less than 5 minutes ago."""
    if pos.created_at is None:
        return False
    age = now_ist() - pos.created_at
    return age < _STALE_GRACE_PERIOD


async def _close_stale_shadow(db: AsyncSession, pos: Position) -> dict:
    """Close a shadow position whose contract price is no longer available.

    Uses entry_price as exit so PnL = 0 — better than leaving it OPEN forever.
    """
    logger.info(
        "Closing stale shadow position %s (no price available after grace period), PnL zeroed",
        pos.symbol,
    )
    return await _close_position(
        db, pos, pos.entry_price, ExitReason.STALE_DATA,
        AgentActionType.TIME_EXIT, requires_confirmation=False,
        fill_at_market=False,
    )


async def _fetch_option_price_rest(fyers_symbol: str) -> dict | None:
    """Fallback: fetch option LTP via Fyers REST when not in Redis cache.

    Retries up to 2 times on transient network/HTTP errors before giving up.
    """
    async def _try_fetch() -> dict | None:
        from app.services.option_resolver import fetch_option_premium

        premium = await fetch_option_premium(fyers_symbol)
        if premium and premium > 0:
            return {"ltp": premium}
        return None

    try:
        return await async_retry(
            _try_fetch,
            retries=2,
            base_delay=0.5,
            label=f"option_price_rest:{fyers_symbol}",
        )
    except Exception:
        logger.exception("Failed REST fallback for %s after retries", fyers_symbol)
        return None
