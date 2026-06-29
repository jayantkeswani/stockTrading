"""Intraday Hunter signal emission — turns a Call 2 ENTER into tradeable option signals.

After the agent decides ENTER, this builds one OPTION ``StrategySignal`` per basket leg
(index), resolves the real contract + current premium via ``option_resolver``, derives an
index-anchored 1:1 SL/target capped to a sane premium band, and hands each signal to the
shared pipeline (``strategy_runner._handle_signal`` → dedup/persist/broadcast + shadow +
YOLO). This is what lets the IH agent feed the existing shadow + paper-YOLO executors
instead of only alerting a human.

SL/target: per leg the stop is an index-anchored adverse move (``IH_STOP_PCT``) converted
to premium by ``option_resolver`` (delta approx), then **clamped** to [IH_PREMIUM_SL_MIN,
IH_PREMIUM_SL_MAX] of premium; the target is set 1:1 on premium. See the SL/target note in
docs/ai/intraday-hunter-agent.md.

Index direction != option win-rate (the S7 lesson): these signals are PAPER-validated
(shadow always 1 lot; a fixed-qty YOLO profile in parallel) before any capital.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import Date, cast, func, select

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.models.signal import Signal
from app.services.option_resolver import resolve_option_details
from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

# Index-anchored 1:1 SL/target params.
IH_STOP_PCT = 0.004        # adverse index move defining the stop (~0.4% ≈ ~30% premium at ATM)
IH_PREMIUM_SL_FALLBACK = 0.30  # premium SL fraction if the resolver can't use index levels
IH_PREMIUM_SL_MIN = 0.20   # clamp the premium stop to [20%, 35%] of premium
IH_PREMIUM_SL_MAX = 0.35
_INDEX_SET = {"NIFTY", "BANKNIFTY", "SENSEX"}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


async def _already_emitted_today(session, trading_date: date) -> bool:
    """True if an IH signal already exists for `trading_date` (once-per-day guard)."""
    res = await session.execute(
        select(func.count(Signal.id)).where(
            Signal.strategy_name == StrategyName.INTRADAY_HUNTER.value,
            cast(func.timezone("Asia/Kolkata", Signal.generated_at), Date) == trading_date,
        )
    )
    return (res.scalar() or 0) > 0


async def emit_signals_for_enter(session, run, live: dict) -> list[str]:
    """Emit one option signal per basket leg for an ENTER decision. Idempotent per day.

    `run` is the IntradayHunterRun (carries `call2_json` with legs/direction/confidence);
    `live` is the Call 2 live-state dict (per-index `last_price`). Returns the list of index
    symbols a signal was emitted for (empty on skip/failure — never raises into the caller).
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
    for leg in legs:
        index = (leg.get("index") or "").upper()
        if index not in _INDEX_SET:
            logger.warning("intraday_hunter: leg with unknown index %r — skip", leg.get("index"))
            continue
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

        # Index-anchored 1:1 levels (CE: stop below / target above; PE: mirror).
        if opt_type == "CE":
            index_sl = spot * (1 - IH_STOP_PCT)
            index_target = spot * (1 + IH_STOP_PCT)
        else:
            index_sl = spot * (1 + IH_STOP_PCT)
            index_target = spot * (1 - IH_STOP_PCT)

        resolution = await resolve_option_details(
            symbol=index,
            index_price=spot,
            signal_type=signal_type,
            sl_pct=IH_PREMIUM_SL_FALLBACK,
            rr_multiplier=1.0,
            index_sl=index_sl,
            index_target=index_target,
        )
        if resolution is None:
            logger.warning("intraday_hunter: could not resolve option for %s %s @ %.2f — skip leg",
                           index, opt_type, spot)
            continue

        # Subscribe the contract to the WS feed so live ticks flow into Redis BEFORE the
        # shadow/YOLO trade opens — the trade monitor reads LTP from Redis for SL/target/MTM.
        # IH bypasses strategy_runner._resolve_option (which normally does this), so we must
        # subscribe here or the position would never get live prices (stale-data / no exits).
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client
            await fyers_ws_client.subscribe_symbols([resolution.fyers_option_symbol])
        except Exception:  # noqa: BLE001 — never block the signal on a subscribe hiccup
            logger.warning("intraday_hunter: could not subscribe %s to WS",
                           resolution.fyers_option_symbol)

        premium = float(resolution.option_premium)
        # Clamp the premium stop to a sane band, then set the target 1:1 on premium.
        raw_sl_frac = (premium - float(resolution.sl_price)) / premium if premium > 0 else IH_PREMIUM_SL_FALLBACK
        sl_frac = _clamp(raw_sl_frac, IH_PREMIUM_SL_MIN, IH_PREMIUM_SL_MAX)
        stop_loss = round(premium * (1 - sl_frac), 2)
        target_price = round(premium + (premium - stop_loss), 2)  # 1:1 on premium

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
            reason=f"Intraday Hunter {opt_type} basket — {thesis}",
            indicators={
                "setup_type": setup_type,
                "index_sl": round(index_sl, 2),
                "index_target": round(index_target, 2),
                "index_name": index,
                "trapped_side": trapped_side,
                "ih_direction": opt_type,
                "ih_thesis": thesis,
                "ih_decision_at": call2.get("_at"),
                "premium_sl_frac": round(sl_frac, 4),
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
            emitted.append(index)
            logger.info(
                "intraday_hunter: emitted %s %s signal — %s entry=%.2f sl=%.2f(%.0f%%) tgt=%.2f conf=%.0f",
                index, opt_type, resolution.fyers_option_symbol, premium, stop_loss,
                sl_frac * 100, target_price, confidence,
            )
        except Exception:  # noqa: BLE001 — one bad leg must not abort the others
            logger.exception("intraday_hunter: _handle_signal failed for %s %s", index, opt_type)

    return emitted
