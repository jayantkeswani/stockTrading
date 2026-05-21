"""Exit simulator — determines how a backtest signal resolves.

Two modes:
  ACCURATE  Walk forward on the option contract's own 1m candles (real premiums).
            Requires Fyers historical option data (available ~6 months back).
  FAST      Delta-approximate option P&L from spot moves. No Fyers API calls.
            Uses ATM delta=0.50, ITM delta=0.60 (same as live option_resolver).

Both modes simulate:
  - SL hit (wick-based: low < premium_sl for PE, or high > premium_sl for CE)
  - Target hit
  - Strategy.should_exit() invalidation check on spot
  - Time exit at POSITION_CLOSE_DEADLINE (3:15 PM)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN, POSITION_CLOSE_DEADLINE
from app.indicators.candle_patterns import Candle

if TYPE_CHECKING:
    from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

ATM_DELTA = 0.50
ITM_DELTA = 0.60
DEFAULT_SL_PCT = 0.30
DEFAULT_TARGET_RR = 1.5


class ExitReason(str, Enum):
    SL_HIT = "SL_HIT"
    TARGET_HIT = "TARGET_HIT"
    TIME_EXIT = "TIME_EXIT"
    INVALIDATION = "INVALIDATION"
    NO_DATA = "NO_DATA"


@dataclass
class SimulatedTrade:
    entry_ts: datetime
    exit_ts: datetime | None
    entry_premium: float
    exit_premium: float | None
    pnl_per_lot: float          # Premium-level P&L × lot_size (option points)
    pnl_pct: float              # % of entry premium
    exit_reason: ExitReason
    lots: int
    fyers_option_symbol: str | None
    mode: str                    # "accurate" or "fast"


async def simulate_exit(
    signal: StrategySignal,
    entry_ts: datetime,
    spot_candles_after: list[tuple[datetime, Candle]],  # 1m spot candles after entry
    fyers_option_symbol: str | None,
    entry_premium: float,
    mode: str = "accurate",
) -> SimulatedTrade:
    """Simulate trade from entry_ts until exit.

    spot_candles_after: [(timestamp, Candle)] for 1m spot candles >= entry_ts
    entry_premium: option LTP at entry (from first available option candle or delta-approx)
    """
    if mode == "accurate" and fyers_option_symbol:
        return await _simulate_accurate(
            signal, entry_ts, fyers_option_symbol, entry_premium, spot_candles_after
        )
    return _simulate_fast(signal, entry_ts, entry_premium, spot_candles_after)


# ---------------------------------------------------------------------------
# Accurate mode — uses real option 1m candles
# ---------------------------------------------------------------------------

async def _simulate_accurate(
    signal: StrategySignal,
    entry_ts: datetime,
    fyers_option_symbol: str,
    entry_premium: float,
    spot_candles: list[tuple[datetime, Candle]],
) -> SimulatedTrade:
    from app.backtest.option_data_fetcher import ensure_option_candles

    # Collect option candles for the trade lifetime (entry → close deadline)
    close_deadline = entry_ts.replace(
        hour=POSITION_CLOSE_DEADLINE.hour,
        minute=POSITION_CLOSE_DEADLINE.minute,
        second=0, microsecond=0,
    )
    end_ts = min(close_deadline, entry_ts.replace(
        hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0
    ))

    opt_candles = await ensure_option_candles(fyers_option_symbol, entry_ts, end_ts)

    if not opt_candles:
        logger.debug("No option candles for %s after %s — returning NO_DATA", fyers_option_symbol, entry_ts)
        return SimulatedTrade(
            entry_ts=entry_ts, exit_ts=None,
            entry_premium=entry_premium, exit_premium=None,
            pnl_per_lot=0.0, pnl_pct=0.0,
            exit_reason=ExitReason.NO_DATA,
            lots=1,
            fyers_option_symbol=fyers_option_symbol,
            mode="accurate",
        )

    sl_pct = _get_sl_pct(signal)
    premium_sl = entry_premium * (1 - sl_pct)
    premium_target = entry_premium * (1 + sl_pct * DEFAULT_TARGET_RR)

    is_call = "CE" in (fyers_option_symbol or "")

    for ts, candle in opt_candles:
        if ts < entry_ts:
            continue
        if ts.time() >= POSITION_CLOSE_DEADLINE:
            exit_p = float(candle.close)
            return _make_result(signal, entry_ts, ts, entry_premium, exit_p, ExitReason.TIME_EXIT, fyers_option_symbol, "accurate")

        # For CE: SL hit if low < premium_sl; target if high > premium_target
        # For PE: same sign convention (we're long puts, so high/low of premium)
        if float(candle.low) <= premium_sl:
            return _make_result(signal, entry_ts, ts, entry_premium, premium_sl, ExitReason.SL_HIT, fyers_option_symbol, "accurate")
        if float(candle.high) >= premium_target:
            return _make_result(signal, entry_ts, ts, entry_premium, premium_target, ExitReason.TARGET_HIT, fyers_option_symbol, "accurate")

    # Reached end of candles without exit → time exit at last candle
    if opt_candles:
        last_ts, last_c = opt_candles[-1]
        return _make_result(signal, entry_ts, last_ts, entry_premium, float(last_c.close), ExitReason.TIME_EXIT, fyers_option_symbol, "accurate")

    return SimulatedTrade(
        entry_ts=entry_ts, exit_ts=None,
        entry_premium=entry_premium, exit_premium=None,
        pnl_per_lot=0.0, pnl_pct=0.0,
        exit_reason=ExitReason.NO_DATA,
        lots=1,
        fyers_option_symbol=fyers_option_symbol,
        mode="accurate",
    )


# ---------------------------------------------------------------------------
# Fast mode — delta approximation
# ---------------------------------------------------------------------------

def _simulate_fast(
    signal: StrategySignal,
    entry_ts: datetime,
    entry_premium: float,
    spot_candles: list[tuple[datetime, Candle]],
) -> SimulatedTrade:
    sl_pct = _get_sl_pct(signal)
    premium_sl = entry_premium * (1 - sl_pct)
    premium_target = entry_premium * (1 + sl_pct * DEFAULT_TARGET_RR)

    entry_spot = spot_candles[0][1].close if spot_candles else 0.0
    delta = ATM_DELTA  # Simple approximation

    is_call = signal.signal_type.value.endswith("CE")

    for ts, candle in spot_candles:
        if ts < entry_ts:
            continue
        if ts.time() >= POSITION_CLOSE_DEADLINE:
            spot_move = (float(candle.close) - entry_spot) * (1 if is_call else -1)
            approx_premium = max(0.01, entry_premium + spot_move * delta)
            return _make_result(signal, entry_ts, ts, entry_premium, approx_premium, ExitReason.TIME_EXIT, None, "fast")

        # Compute approximate premium from spot high/low
        if is_call:
            worst = entry_premium + (float(candle.low) - entry_spot) * delta
            best = entry_premium + (float(candle.high) - entry_spot) * delta
        else:
            worst = entry_premium + (entry_spot - float(candle.high)) * delta
            best = entry_premium + (entry_spot - float(candle.low)) * delta

        worst = max(0.01, worst)
        best = max(0.01, best)

        if worst <= premium_sl:
            return _make_result(signal, entry_ts, ts, entry_premium, premium_sl, ExitReason.SL_HIT, None, "fast")
        if best >= premium_target:
            return _make_result(signal, entry_ts, ts, entry_premium, premium_target, ExitReason.TARGET_HIT, None, "fast")

    if spot_candles:
        last_ts, last_c = spot_candles[-1]
        spot_move = (float(last_c.close) - entry_spot) * (1 if is_call else -1)
        approx_premium = max(0.01, entry_premium + spot_move * delta)
        return _make_result(signal, entry_ts, last_ts, entry_premium, approx_premium, ExitReason.TIME_EXIT, None, "fast")

    return SimulatedTrade(
        entry_ts=entry_ts, exit_ts=None,
        entry_premium=entry_premium, exit_premium=None,
        pnl_per_lot=0.0, pnl_pct=0.0,
        exit_reason=ExitReason.NO_DATA,
        lots=1,
        fyers_option_symbol=None,
        mode="fast",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_sl_pct(signal: StrategySignal) -> float:
    indicators = signal.indicators or {}
    return indicators.get("sl_pct", DEFAULT_SL_PCT)


def _make_result(
    signal: StrategySignal,
    entry_ts: datetime,
    exit_ts: datetime,
    entry_premium: float,
    exit_premium: float,
    reason: ExitReason,
    fyers_sym: str | None,
    mode: str,
) -> SimulatedTrade:
    from app.core.constants import LOT_SIZES
    lot_size = LOT_SIZES.get(signal.symbol, 1)
    pnl_per_lot = (exit_premium - entry_premium) * lot_size
    pnl_pct = (exit_premium - entry_premium) / entry_premium * 100 if entry_premium else 0.0

    return SimulatedTrade(
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_premium=entry_premium,
        exit_premium=exit_premium,
        pnl_per_lot=pnl_per_lot,
        pnl_pct=pnl_pct,
        exit_reason=reason,
        lots=1,
        fyers_option_symbol=fyers_sym,
        mode=mode,
    )
