"""Intraday Hunter signal emission — turns a Call 2 ENTER into tradeable option signals.

After the agent decides ENTER, this builds OPTION ``StrategySignal``s from the basket and
hands each to the shared pipeline (``strategy_runner._handle_signal`` → dedup/persist/
broadcast + shadow + YOLO). This is what lets the IH agent feed the existing shadow +
paper-YOLO executors instead of only alerting a human.

Strike structure (fixed, independent of the LLM's strike hint): per traded index we open
``_IH_LEG_STRUCTURE`` legs at specific ITM depths — **BANKNIFTY = two legs (ITM-2 + ITM-1)**,
NIFTY = ITM-1, SENSEX = ITM-1. Lots are fixed per leg at execution (see lot_sizing).

SL/target: symmetric **1:1**, sized as ``IH_SL_TGT_PCT`` of the entry premium (a single knob
that auto-scales the rupee move with the option price). That premium move is also converted to
an index-points distance via a delta approximation and stored as ``index_sl``/``index_target``
on the signal (informational context — e.g. for the Basket & Exits UI card) — the trade monitor
exits each leg on the **option premium** ``stop_loss``/``target_price`` fields via the normal
per-position SL/target check, same as every other strategy.

Index direction != option win-rate (the S7 lesson): these signals are PAPER-validated
(shadow always 1 lot; a fixed-qty YOLO profile in parallel) before any capital.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import Date, cast, func, select

from app.core.constants import STRIKE_GAPS
from app.core.enums import InstrumentType, SignalType, StrategyName
from app.models.signal import Signal
from app.services.option_resolver import resolve_option_details
from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

# Symmetric (1:1) SL/target sized as a fraction of the entry premium. Auto-scales the rupee
# move with the option price (e.g. ~60% → 180 pts on a 300 premium, 360 on a 600). Widened from
# 0.30 (2×) — real premium paths showed the tighter stop chopped winners that keep running well
# past a 30% target and got stopped out well before a 30% adverse move exhausted.
IH_SL_TGT_PCT = 0.60
# Per-index leg structure as ITM depths (0 = ATM, 1 = ITM-1, 2 = ITM-2). BANKNIFTY trades two
# legs (a deeper ITM-2 + an ITM-1); NIFTY and SENSEX a single ITM-1 leg.
_IH_LEG_STRUCTURE: dict[str, list[int]] = {
    "BANKNIFTY": [2, 1],
    "NIFTY": [1],
    "SENSEX": [1],
}
_IH_DEFAULT_DEPTHS = [1]
# Delta approximation by ITM depth (premium pts ≈ delta × index pts), to convert the desired
# premium move into the index-points SL/target distance the monitor validates against.
_IH_DELTA_BY_DEPTH = {0: 0.50, 1: 0.60, 2: 0.68}
_INDEX_SET = {"NIFTY", "BANKNIFTY", "SENSEX"}


async def _already_emitted_today(session, trading_date: date) -> bool:
    """True if an IH signal already exists for `trading_date` (once-per-day guard)."""
    res = await session.execute(
        select(func.count(Signal.id)).where(
            Signal.strategy_name == StrategyName.INTRADAY_HUNTER.value,
            cast(func.timezone("Asia/Kolkata", Signal.generated_at), Date) == trading_date,
        )
    )
    return (res.scalar() or 0) > 0


def _resolved_depth(symbol: str, strike: float, spot: float) -> int:
    """How many strikes ITM the resolved strike landed (0 = ATM), for the delta lookup."""
    gap = STRIKE_GAPS.get(symbol)
    if not gap or gap <= 0:
        return 1
    atm = round(spot / gap) * gap
    return int(round(abs(strike - atm) / gap))


async def emit_signals_for_enter(session, run, live: dict) -> list[str]:
    """Emit option signals for an ENTER decision (one per index leg × ITM depth). Idempotent per day.

    `run` is the IntradayHunterRun (carries `call2_json` with legs/direction/confidence);
    `live` is the Call 2 live-state dict (per-index `last_price`). Returns the list of leg
    labels a signal was emitted for (empty on skip/failure — never raises into the caller).
    """
    call2 = run.call2_json or {}
    if (call2.get("decision") or "").upper() != "ENTER":
        return []
    legs = call2.get("legs") or []
    if not legs:
        logger.warning("intraday_hunter: ENTER with no legs — nothing to emit")
        return []

    if await _already_emitted_today(session, run.trading_date):
        logger.info("intraday_hunter: signals already emitted for %s — skip re-emit", run.trading_date)
        return []

    live_idx = (live or {}).get("indices", {})
    confidence = float(run.confidence or 0)
    thesis = (call2.get("thesis") or "Intraday Hunter ENTER").strip()
    setup_type = (call2.get("regime") or "intraday_hunter").strip()
    trapped_side = call2.get("trapped_side")

    emitted: list[str] = []
    seen_indices: set[str] = set()
    for leg in legs:
        index = (leg.get("index") or "").upper()
        if index not in _INDEX_SET:
            logger.warning("intraday_hunter: leg with unknown index %r — skip", leg.get("index"))
            continue
        if index in seen_indices:  # one structure per index even if the LLM repeats it
            continue
        seen_indices.add(index)
        opt_type = (leg.get("option_type") or call2.get("direction") or "").upper()
        if opt_type not in ("CE", "PE"):
            logger.warning("intraday_hunter: %s leg with bad option_type %r — skip", index, opt_type)
            continue
        spot = (live_idx.get(index) or {}).get("last_price")
        if not spot or float(spot) <= 0:
            logger.warning("intraday_hunter: no live spot for %s — skip leg", index)
            continue
        spot = float(spot)
        signal_type = SignalType.BUY_CE if opt_type == "CE" else SignalType.BUY_PE

        for depth in _IH_LEG_STRUCTURE.get(index, _IH_DEFAULT_DEPTHS):
            label = await _emit_leg(
                session, index, opt_type, signal_type, spot, depth,
                confidence, thesis, setup_type, trapped_side, call2,
            )
            if label:
                emitted.append(label)

    return emitted


async def _emit_leg(
    session, index: str, opt_type: str, signal_type, spot: float, depth: int,
    confidence: float, thesis: str, setup_type: str, trapped_side, call2: dict,
) -> str | None:
    """Resolve and emit ONE option leg at the requested ITM depth. Returns its label or None."""
    # Resolve the contract at the requested depth (fall back one strike toward ATM if the
    # exact strike has no live premium).
    resolution = await resolve_option_details(
        symbol=index,
        index_price=spot,
        signal_type=signal_type,
        sl_pct=IH_SL_TGT_PCT,
        rr_multiplier=1.0,
        itm_offsets=(depth, max(depth - 1, 0)),
    )
    if resolution is None:
        logger.warning("intraday_hunter: could not resolve %s %s ITM-%d @ %.2f — skip leg",
                       index, opt_type, depth, spot)
        return None

    # Subscribe the contract to the WS feed so live ticks flow into Redis BEFORE the trade
    # opens — the monitor reads the option LTP (MTM + close fill) and the index spot (exit)
    # from Redis. IH bypasses strategy_runner._resolve_option (which normally subscribes), so
    # we must subscribe here or the position would never get live prices.
    try:
        from app.data_feed.fyers_ws_client import fyers_ws_client
        await fyers_ws_client.subscribe_symbols([resolution.fyers_option_symbol])
    except Exception:  # noqa: BLE001 — never block the signal on a subscribe hiccup
        logger.warning("intraday_hunter: could not subscribe %s to WS",
                       resolution.fyers_option_symbol)

    premium = float(resolution.option_premium)
    resolved_depth = _resolved_depth(index, float(resolution.strike_price), spot)
    # Symmetric premium move = IH_SL_TGT_PCT of premium; convert to an index distance via delta.
    premium_move = IH_SL_TGT_PCT * premium
    delta = _IH_DELTA_BY_DEPTH.get(min(resolved_depth, 2), 0.60)
    index_move = premium_move / delta if delta > 0 else premium_move * 2

    if opt_type == "CE":
        index_sl = spot - index_move
        index_target = spot + index_move
    else:  # PE — mirror
        index_sl = spot + index_move
        index_target = spot - index_move

    # Premium stop/target are display-only (the monitor validates the exit on the index).
    stop_loss = round(premium - premium_move, 2)
    target_price = round(premium + premium_move, 2)  # 1:1 on premium

    signal = StrategySignal(
        strategy_name=StrategyName.INTRADAY_HUNTER,
        symbol=index,
        signal_type=signal_type,
        instrument_type=InstrumentType.OPTION,
        strike_price=float(resolution.strike_price),
        expiry_date=resolution.expiry_date,
        entry_price=premium,
        stop_loss=stop_loss,
        target_price=target_price,
        confidence=confidence,
        reason=f"Intraday Hunter {opt_type} basket (ITM-{resolved_depth}) — {thesis}",
        indicators={
            "setup_type": setup_type,
            "index_sl": round(index_sl, 2),
            "index_target": round(index_target, 2),
            "index_name": index,
            "trapped_side": trapped_side,
            "ih_direction": opt_type,
            "ih_thesis": thesis,
            "ih_decision_at": call2.get("_at"),
            "ih_itm_depth": resolved_depth,
            "ih_sl_tgt_pct": IH_SL_TGT_PCT,
            "is_permanent_watchlist": False,
        },
        index_sl=round(index_sl, 2),
        index_target=round(index_target, 2),
        index_entry_price=spot,
        fyers_option_symbol=resolution.fyers_option_symbol,
        option_resolved=True,
    )

    try:
        from app.services.strategy_runner import strategy_runner
        await strategy_runner._handle_signal(signal, executable=True, blocked_reason=None)
        label = f"{index} {int(resolution.strike_price)}{opt_type} (ITM-{resolved_depth})"
        logger.info(
            "intraday_hunter: emitted %s — %s entry=%.2f sl=%.2f tgt=%.2f "
            "index_sl=%.2f index_tgt=%.2f conf=%.0f",
            label, resolution.fyers_option_symbol, premium, stop_loss, target_price,
            index_sl, index_target, confidence,
        )
        return label
    except Exception:  # noqa: BLE001 — one bad leg must not abort the others
        logger.exception("intraday_hunter: _handle_signal failed for %s %s ITM-%d",
                         index, opt_type, depth)
        return None
