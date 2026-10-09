"""Counterfactual premium-path capture — subscribe ATM ±2 strikes (CE + PE) per index daily.

At ~09:10 IST (and again just after the open, to re-centre on the real open) subscribe the
nearest-expiry ATM±`capture_strikes_each_side` CE and PE contracts for NIFTY / BANKNIFTY / SENSEX
(nearest weekly for NIFTY + SENSEX, monthly for BANKNIFTY via option_resolver.select_expiry) —
~30 contracts, EVERY trading day including skip days. `feed_manager` then persists their 1m
candles into `market_data_1m` (symbol = the Fyers option symbol), which lets grading replay any
arm's basket on REAL premiums. Fyers' WS limit is 5000 symbols — far above this.

The day's list is stored in Redis `ih_v2:capture:{date}` (read back by `fyers_ws_client.
_collect_dynamic_symbols` so it survives WS reconnects, and by grading). Every captured
contract and the index futures are registered with the order-flow tracker (the minute log reads
the captured strike nearest the live spot); 5-level depth (flag) is subscribed for the ATM only.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from app.core.constants import STRIKE_GAPS
from app.core.redis import get_cached_price, get_redis
from app.core.utils import now_ist
from app.services.intraday_hunter_v2.params import v2_active, v2_params_async
from app.services.option_resolver import find_option_symbol, select_expiry

logger = logging.getLogger(__name__)

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
_TTL = 3 * 24 * 3600


def capture_key(d: date) -> str:
    return f"ih_v2:capture:{d.isoformat()}"


def ladder(spot: float, gap: float, each_side: int) -> list[float]:
    """ATM ± each_side strikes (ATM = spot rounded to the strike gap)."""
    atm = round(spot / gap) * gap
    return [float(atm + k * gap) for k in range(-each_side, each_side + 1)]


async def _spot(index: str) -> float | None:
    data = await get_cached_price(index)
    try:
        v = float((data or {}).get("ltp") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


async def load_capture(d: date) -> dict:
    """The stored capture for `d`: {symbols: [...], contracts: {index: [{strike, type, symbol}]}}."""
    raw = await get_redis().get(capture_key(d))
    return json.loads(raw) if raw else {"symbols": [], "contracts": {}}


async def capture_atm_ladder(reason: str = "scheduled") -> dict:
    """Resolve + subscribe the ATM±N CE/PE ladder for every index; merge into today's capture."""
    from app.data_feed.fyers_ws_client import fyers_ws_client
    from app.services.intraday_hunter_v2.orderflow import orderflow_tracker

    if not await v2_active():
        return {"added": 0, "total": 0, "symbols": [], "skipped": "kill switch off"}
    params = await v2_params_async()
    each = int(params.get("capture_strikes_each_side", 2))
    today = now_ist().date()
    cap = await load_capture(today)
    known = set(cap["symbols"])
    new_syms: list[str] = []
    atm_syms: list[str] = []

    for index in INDICES:
        spot = await _spot(index)
        gap = STRIKE_GAPS.get(index)
        if not spot or not gap:
            logger.warning("ih_v2 capture: no spot/gap for %s — skipping", index)
            continue
        expiry = select_expiry(index)
        atm = round(spot / gap) * gap
        for strike in ladder(spot, gap, each):
            for opt in ("CE", "PE"):
                sym = await find_option_symbol(index, strike, expiry, opt)
                if not sym:
                    continue
                if sym not in known:
                    known.add(sym)
                    new_syms.append(sym)
                    cap["contracts"].setdefault(index, []).append(
                        {"strike": strike, "type": opt, "symbol": sym, "expiry": expiry.isoformat()}
                    )
                if strike == atm:
                    atm_syms.append(sym)
        orderflow_tracker.track([f"{index}_FUT"])

    cap["symbols"] = sorted(known)
    # Order flow covers the WHOLE captured ladder: the minute log reads the captured strike
    # nearest the live spot, which drifts off the capture-time ATM within minutes.
    orderflow_tracker.track(cap["symbols"])
    cap["updated_at"] = now_ist().isoformat()
    await get_redis().set(capture_key(today), json.dumps(cap), ex=_TTL)
    if new_syms:
        await fyers_ws_client.subscribe_symbols(new_syms)
        from app.config import settings
        if settings.ih_v2_depth_enabled and hasattr(fyers_ws_client, "subscribe_depth"):
            await fyers_ws_client.subscribe_depth(
                [s for s in new_syms if s in atm_syms]
            )
    logger.info("ih_v2 capture (%s): +%d contracts, %d total", reason, len(new_syms), len(known))
    return {"added": len(new_syms), "total": len(known), "symbols": cap["symbols"]}
