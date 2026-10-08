"""IH v2 signal emission — turns a v2 Call 2 ENTER into tradeable option signals.

Basket shape = the teacher's: BANKNIFTY ATM + 1 OTM, NIFTY ATM, SENSEX ATM (`leg_structure`
param; depth 0 = ATM, -1 = OTM-1 via option_resolver). One `intraday_hunter_v2` OPTION signal per
leg into the shared pipeline (`strategy_runner._handle_signal` → persist/broadcast + shadow +
YOLO). The `IH-v2` YOLO profile (fixed 2 lots/leg, lot_sizing) and the shadow book (1 lot) open
the basket together; exits are BASKET-level in trade_monitor.

The per-leg `stop_loss`/`target_price` are INFORMATIONAL — the leg's share of the basket band
(premium x (1 ∓ basket_tp_sl_pct)); trade_monitor bypasses the per-leg checks for v2 legs.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import Date, cast, func, select

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.models.signal import Signal
from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS, v2_active
from app.services.option_resolver import resolve_option_details
from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

INDEX_SET = ("NIFTY", "BANKNIFTY", "SENSEX")


def depth_label(depth: int) -> str:
    """0 → 'ATM', -1 → 'OTM-1', 2 → 'ITM-2'."""
    if depth == 0:
        return "ATM"
    return f"OTM-{-depth}" if depth < 0 else f"ITM-{depth}"


def traded_indices(call2: dict) -> list[str]:
    """Indices the basket trades: the LLM's legs (minus exclusions), default all three."""
    excluded = {(e.get("index") or "").upper() for e in (call2.get("excluded_indices") or [])
                if isinstance(e, dict)}
    named = [(leg.get("index") or "").upper() for leg in (call2.get("legs") or [])
             if isinstance(leg, dict)]
    picked = [i for i in INDEX_SET if i in named] or list(INDEX_SET)
    return [i for i in picked if i not in excluded]


async def already_emitted_today(session, trading_date: date) -> bool:
    """True if a v2 signal already exists for `trading_date` (once-per-day guard)."""
    res = await session.execute(
        select(func.count(Signal.id)).where(
            Signal.strategy_name == StrategyName.INTRADAY_HUNTER_V2.value,
            cast(func.timezone("Asia/Kolkata", Signal.generated_at), Date) == trading_date,
        )
    )
    return (res.scalar() or 0) > 0


async def emit_signals_for_enter(session, run, spots: dict[str, float], params: dict) -> list[str]:
    """Emit one signal per basket leg for a v2 ENTER. Idempotent per day; never raises.

    `spots` = {index: live spot}. Returns the emitted leg labels.
    """
    call2 = run.call2_json or {}
    if (call2.get("decision") or "").upper() != "ENTER":
        return []
    direction = (call2.get("direction") or "").upper()
    if direction not in ("CE", "PE"):
        logger.warning("ih_v2: ENTER without a CE/PE direction — nothing to emit")
        return []
    if not await v2_active(fail_closed=False):  # stop only on an explicit kill
        logger.warning("ih_v2: kill switch is OFF (strategy_configs.is_active=false) — no signals")
        return []
    if await already_emitted_today(session, run.trading_date):
        logger.info("ih_v2: signals already emitted for %s — skip re-emit", run.trading_date)
        return []

    structure = params.get("leg_structure") or INTRADAY_HUNTER_V2_DEFAULTS["leg_structure"]
    pct = float(params.get("basket_tp_sl_pct", INTRADAY_HUNTER_V2_DEFAULTS["basket_tp_sl_pct"]))
    signal_type = SignalType.BUY_CE if direction == "CE" else SignalType.BUY_PE
    emitted: list[str] = []
    for index in traded_indices(call2):
        spot = spots.get(index)
        if not spot or float(spot) <= 0:
            logger.warning("ih_v2: no live spot for %s — skip leg", index)
            continue
        for depth in structure.get(index, [0]):
            label = await _emit_leg(index, direction, signal_type, float(spot), int(depth),
                                    pct, run, call2)
            if label:
                emitted.append(label)
    return emitted


async def _emit_leg(index, direction, signal_type, spot, depth, pct, run, call2) -> str | None:
    """Resolve + subscribe + emit ONE leg at exactly `depth` (no fallback strike — a fallback
    would collide with the ATM leg). Returns its label or None."""
    resolution = await resolve_option_details(
        symbol=index, index_price=spot, signal_type=signal_type,
        sl_pct=pct, rr_multiplier=1.0, itm_offsets=(depth,),
    )
    if resolution is None:
        logger.warning("ih_v2: could not resolve %s %s %s @ %.2f — skip leg",
                       index, direction, depth_label(depth), spot)
        return None
    try:  # ticks must flow before the position opens (v2 bypasses _resolve_option)
        from app.data_feed.fyers_ws_client import fyers_ws_client
        await fyers_ws_client.subscribe_symbols([resolution.fyers_option_symbol])
    except Exception:  # noqa: BLE001
        logger.warning("ih_v2: could not subscribe %s", resolution.fyers_option_symbol)

    premium = float(resolution.option_premium)
    gates = call2.get("_gates") or {}
    signal = StrategySignal(
        strategy_name=StrategyName.INTRADAY_HUNTER_V2,
        symbol=index,
        signal_type=signal_type,
        instrument_type=InstrumentType.OPTION,
        strike_price=float(resolution.strike_price),
        expiry_date=resolution.expiry_date,
        entry_price=premium,
        # Informational per-leg share of the basket band — v2 legs exit at the BASKET level.
        stop_loss=round(premium * (1 - pct), 2),
        target_price=round(premium * (1 + pct), 2),
        confidence=float(run.confidence or 0),
        reason=f"IH v2 {direction} basket ({depth_label(depth)}) — "
               f"{(call2.get('thesis') or 'stop-hunt ride').strip()}",
        indicators={
            "setup_type": "ih_v2",
            "index_name": index,
            "ih_direction": direction,
            "ih_depth": depth,
            "ih_leg": depth_label(depth),
            "ih_decision_at": call2.get("_at"),
            "ih_pool_broken": call2.get("pool_broken"),
            "ih_opening_type": call2.get("_opening_type"),
            "ih_call2_latency_ms": call2.get("_latency_ms"),
            "ih_gates": gates,
            "ih_basket_tp_sl_pct": pct,
            "is_permanent_watchlist": False,
        },
        index_entry_price=spot,
        fyers_option_symbol=resolution.fyers_option_symbol,
        option_resolved=True,
    )
    try:
        from app.services.strategy_runner import strategy_runner
        await strategy_runner._handle_signal(signal, executable=True, blocked_reason=None)
    except Exception:  # noqa: BLE001 — one bad leg must not abort the others
        logger.exception("ih_v2: _handle_signal failed for %s %s", index, depth_label(depth))
        return None
    label = f"{index} {int(resolution.strike_price)}{direction} ({depth_label(depth)})"
    logger.info("ih_v2: emitted %s — %s entry=%.2f conf=%s",
                label, resolution.fyers_option_symbol, premium, run.confidence)
    return label
