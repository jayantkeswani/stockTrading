"""Strategy 7: VWAP Reclaim (index options) — the S2 entry redesign, ships DARK.

Fixes Strategy 2's root-cause weakness — it fires on the reversal *candle* at VWAP,
which is exhaustion: 55% of S2's losing entries never go green and stop within ~21 min
(`docs/backtest/s2-signal-accuracy-study.md`). This is the index-options analogue of the
S5→S6 move. Instead of buying the reversal candle, this strategy treats it as an ARM and
fires only on a 1m **reclaim** that confirms the bounce/rejection held:

    1. ARM     — S2's own trigger fires: price pulled back into the VWAP band (between
                 vwap_min_distance_pct and vwap_proximity_pct), a 5m reversal candle, bias
                 not STRONG-opposed, no volume spike. Record the reversal candle's extreme
                 as the pullback swing and its other end as the reclaim trigger.
    2. RECLAIM — a subsequent 1m candle closes back THROUGH the trigger in the trade
                 direction (CE: close above the reversal candle's high; PE: below its low).
                 This is the "did the bounce hold" gate S2 skips.
    3. FIRE    — entry at the reclaim close; tight stop at the pullback swing extreme
                 (the R:R lever, à la S6); index target by R:R. index_sl / index_target are
                 carried so option_resolver delta-converts them to a tight premium stop.
    4. ABORT   — no reclaim within reclaim_timeout 1m candles, or a 1m slices through the
                 swing extreme before reclaiming (the pullback failed).

State is per (symbol × side), in-memory and ephemeral (re-forms on restart). Evaluated on
every 1m candle close. Reuses S2's VWAP/bias/volume gates and option-resolution path
(instrument_type=OPTION). Confidence is a **lean structural** composite — it deliberately
does NOT reuse S2's `compute_confidence`, which the study proved *inverted* (r −0.224); the
entry runs effectively ungated by confidence and the lean score is recorded for calibration
and later re-weighting from the strategy's own win/loss data.

Offline validation (98 signals, 2026-04-29→06-04, `scripts/replay_strategy2_reclaim.py` +
`analyze_strategy2_signal_accuracy.py --strategy vwap_reclaim`): target-first 15.6%→40.2%
and +30 min forward-direction 52.9%→62.1% (robust in both split halves), vs S2 on the same
index-days. Decisive test is the live shadow real-P&L A/B vs S2. See
`docs/strategies/strategy-2-reclaim-entry.md` (spec) and the study above.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.core.constants import VWAP_MIN_DISTANCE_PCT, VWAP_PROXIMITY_PCT
from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.candle_patterns import (
    Candle,
    average_volume,
    is_bearish_reversal,
    is_bullish_reversal,
)
from app.indicators.intraday_bias import is_blocked_by_bias
from app.indicators.market_levels import select_index_sl_target
from app.indicators.vwap import is_pullback_to_vwap, price_distance_from_vwap
from app.strategies.base import (
    BaseStrategy,
    ExitSignal,
    MarketContext,
    StrategySignal,
)

logger = logging.getLogger(__name__)


@dataclass
class _Arm:
    """A single armed VWAP-reversal awaiting its reclaim, per (symbol × side)."""
    is_long: bool
    swing: float          # pullback swing extreme (reversal-candle low for CE / high for PE)
    trigger: float        # reclaim reference (reversal-candle high for CE / low for PE)
    armed_len_1m: int     # len(candles_1m) when armed (timeout + subsequent-candle gate)
    vwap_at_arm: float
    dist_at_arm: float
    last_processed_len: int = 0   # len(candles_1m) last processed (per-candle idempotency)


def _completed_5m(candles_1m: list[Candle]) -> list[Candle]:
    """Aggregate the in-session 1m series into *completed* 5m candles (drops a partial
    tail). Relies on the 1m series starting at 09:15 (a 5m boundary) so blocks of 5 align
    — same approach as Strategy 6, giving stable 5m-close transition detection from
    candles_1m alone (no dependency on how candles_5m was built)."""
    n_full = (len(candles_1m) // 5) * 5
    out: list[Candle] = []
    for i in range(0, n_full, 5):
        block = candles_1m[i : i + 5]
        out.append(
            Candle(
                open=block[0].open,
                high=max(c.high for c in block),
                low=min(c.low for c in block),
                close=block[-1].close,
                volume=sum(c.volume for c in block),
            )
        )
    return out


class VWAPReclaimStrategy(BaseStrategy):
    """Arm on S2's VWAP-reversal trigger, fire on a 1m reclaim (see module docstring)."""

    name = StrategyName.VWAP_RECLAIM
    holding_type = "INTRADAY"
    max_lots = 5

    def __init__(self) -> None:
        # Per-symbol state (ephemeral; resets on process restart).
        self._arms: dict[str, dict[str, _Arm]] = {}     # symbol → side(CE/PE) → arm
        self._consumed_5m_len: dict[str, dict[str, int]] = {}  # symbol → side → 5m-bar count
        self._pending_logs: list[tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def drain_pending_logs(self) -> list[tuple[str, str]]:
        """Return and clear pending agent-log entries [(category, message), ...]."""
        logs = self._pending_logs
        self._pending_logs = []
        return logs

    def _log(self, category: str, message: str) -> None:
        self._pending_logs.append((category, message))

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Arm on a VWAP-reversal, then fire on a 1m reclaim. One signal max."""
        params = ctx.strategy_params or {}
        sym = ctx.symbol
        self._arms.setdefault(sym, {})
        self._consumed_5m_len.setdefault(sym, {})

        if ctx.vwap is None:
            return None
        candles_1m = ctx.candles_1m or []
        if len(candles_1m) < 5:
            return None
        completed_5m = _completed_5m(candles_1m)
        if len(completed_5m) < 5:   # S2 parity — needs ≥5 completed 5m bars
            return None

        self._try_arm(ctx, params, completed_5m, candles_1m)
        return self._process(ctx, params, completed_5m, candles_1m)

    # ------------------------------------------------------------------
    # Arming
    # ------------------------------------------------------------------

    def _try_arm(
        self, ctx: MarketContext, params: dict,
        completed_5m: list[Candle], candles_1m: list[Candle],
    ) -> None:
        """Arm a side when S2's VWAP-reversal trigger fires (one live arm per side,
        never re-arming the same 5m reversal bar)."""
        sym = ctx.symbol
        v = ctx.vwap.vwap
        price = ctx.current_price
        proximity = params.get("vwap_proximity_pct", VWAP_PROXIMITY_PCT)
        if not is_pullback_to_vwap(price, v, proximity):
            return
        distance = price_distance_from_vwap(price, v)
        if abs(distance) < params.get("vwap_min_distance_pct", VWAP_MIN_DISTANCE_PCT):
            return
        if distance > 0:
            side, is_long = "CE", True
        elif distance < 0:
            side, is_long = "PE", False
        else:
            return

        arms = self._arms[sym]
        consumed = self._consumed_5m_len[sym]
        if side in arms:
            return
        if consumed.get(side) == len(completed_5m):
            return

        if params.get("block_strong_opposing_bias", True) and ctx.intraday_bias is not None:
            if is_blocked_by_bias(side, ctx.intraday_bias):
                return
        reversal = is_bullish_reversal(completed_5m) if is_long else is_bearish_reversal(completed_5m)
        if not reversal:
            return

        # Volume-spike reject (S2): a low-volume pullback is the cleaner setup. Use the
        # futures-volume 5m series for indices (spot volume is ~zero), else completed_5m.
        vol_candles = ctx.candles_5m_futures_volume or completed_5m
        avg_vol = average_volume(vol_candles, periods=20)
        curr_vol = vol_candles[-1].volume if vol_candles else 0
        if avg_vol > 0 and curr_vol > avg_vol * params.get("vol_spike_mult", 1.2):
            return

        rev = completed_5m[-1]
        arms[side] = _Arm(
            is_long=is_long,
            swing=rev.low if is_long else rev.high,
            trigger=rev.high if is_long else rev.low,
            armed_len_1m=len(candles_1m),
            vwap_at_arm=v,
            dist_at_arm=distance,
        )
        consumed[side] = len(completed_5m)
        self._log("ARM", f"{sym}: {side} reversal armed near VWAP {v:.2f} (dist {distance:+.3f}%)")

    # ------------------------------------------------------------------
    # Reclaim / abort
    # ------------------------------------------------------------------

    def _process(
        self, ctx: MarketContext, params: dict,
        completed_5m: list[Candle], candles_1m: list[Candle],
    ) -> StrategySignal | None:
        """Advance every armed side on the latest 1m candle; fire the first reclaim."""
        sym = ctx.symbol
        arms = self._arms[sym]
        cur_len = len(candles_1m)
        c1 = candles_1m[-1]
        reclaim_ref = params.get("reclaim_ref", "reversal_extreme")
        timeout = int(params.get("reclaim_timeout", 5))
        require_green = params.get("require_reclaim_green", True)

        fired: StrategySignal | None = None
        for side in list(arms.keys()):
            arm = arms[side]
            if cur_len <= arm.last_processed_len or cur_len <= arm.armed_len_1m:
                continue  # idempotent; reclaim only on candles after the arm
            arm.last_processed_len = cur_len

            if cur_len - arm.armed_len_1m > timeout:
                del arms[side]
                self._log("ABORT", f"{sym}: {side} reclaim timed out")
                continue
            # Slice-through — price gave up the pullback swing before reclaiming.
            sliced = c1.close < arm.swing if arm.is_long else c1.close > arm.swing
            if sliced:
                del arms[side]
                self._log("ABORT", f"{sym}: {side} sliced through swing {arm.swing:.2f}")
                continue

            ref = ctx.vwap.vwap if (reclaim_ref == "vwap" and ctx.vwap) else arm.trigger
            if arm.is_long:
                reclaimed = c1.close > ref and (not require_green or c1.close > c1.open)
            else:
                reclaimed = c1.close < ref and (not require_green or c1.close < c1.open)
            if not reclaimed:
                continue

            # One reclaim attempt per arm — consume regardless of downstream gates.
            del arms[side]
            signal = self._build_signal(ctx, params, arm, completed_5m)
            if signal is not None and fired is None:
                fired = signal
        return fired

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _build_signal(
        self, ctx: MarketContext, params: dict, arm: _Arm, completed_5m: list[Candle],
    ) -> StrategySignal | None:
        """Tight swing stop + R:R target → index_sl/index_target; lean confidence."""
        sym = ctx.symbol
        is_long = arm.is_long
        direction = "CE" if is_long else "PE"
        signal_type = SignalType.BUY_CE if is_long else SignalType.BUY_PE
        entry = ctx.current_price

        swing = arm.swing
        buf = entry * params.get("swing_buffer_pct", 0.03) / 100.0
        sl = swing - buf if is_long else swing + buf

        risk = entry - sl if is_long else sl - entry
        if risk <= 0:
            self._log("SKIP", f"{sym}: degenerate SL {sl:.2f} vs entry {entry:.2f} ({direction})")
            return None
        min_risk = entry * params.get("min_risk_pct", 0.03) / 100.0
        if risk < min_risk:                       # whipsaw floor
            risk = min_risk
            sl = entry - risk if is_long else entry + risk
        if risk > entry * params.get("max_risk_pct", 1.0) / 100.0:
            self._log("SKIP", f"{sym}: risk {risk:.2f} too wide ({direction})")
            return None

        rr = params.get("rr_multiplier", 1.5)
        target = entry + rr * risk if is_long else entry - rr * risk
        if params.get("target_mode", "rr") == "structure":
            _, struct_tgt = select_index_sl_target(
                entry_price=entry, signal_type=signal_type, vwap=ctx.vwap,
                previous_day=ctx.previous_day, cpr=ctx.cpr,
                oi_analysis=ctx.oi_analysis, candles_5m=completed_5m,
            )
            if struct_tgt is not None and (
                (is_long and struct_tgt > entry) or (not is_long and struct_tgt < entry)
            ):
                target = struct_tgt
        rr_actual = abs(target - entry) / risk if risk > 0 else 0.0

        confidence, factors = self._compute_confidence(ctx, arm, rr_actual, is_long)

        indicators: dict = {
            "setup_type": "VWAP_RECLAIM",
            "entry_style": "RECLAIM",
            "price": round(entry, 2),
            "index_entry_price": round(entry, 2),
            "index_sl": round(sl, 2),
            "index_target": round(target, 2),
            "vwap": round(arm.vwap_at_arm, 2),
            "vwap_distance_pct": round(arm.dist_at_arm, 4),
            "reversal_swing": round(swing, 2),
            "trigger_level": round(arm.trigger, 2),
            "rr": round(rr_actual, 2),
            # Fallbacks for option_resolver if the index levels are ever dropped.
            "sl_pct": params.get("sl_pct_fallback", 0.30),
            "rr_multiplier": rr,
            "confidence_factors": factors,
        }
        self._build_indicator_snapshot(ctx, indicators)
        self._log(
            "SIGNAL",
            f"{sym}: VWAP reclaim {direction} entry {entry:.2f} SL {sl:.2f} "
            f"target {target:.2f} (R:R 1:{rr_actual:.1f}, conf {confidence:.0f})",
        )

        return StrategySignal(
            strategy_name=self.name,
            symbol=sym,
            signal_type=signal_type,
            instrument_type=InstrumentType.OPTION,
            strike_price=0,
            expiry_date=None,
            entry_price=entry,
            stop_loss=0,
            target_price=None,
            confidence=confidence,
            reason=(
                f"VWAP reclaim BUY {direction}: {sym} reclaimed "
                f"{'above' if is_long else 'below'} {arm.trigger:.2f} "
                f"(swing {swing:.2f}, R:R 1:{rr_actual:.1f}, conf {confidence:.0f})"
            ),
            indicators=indicators,
            index_sl=round(sl, 2),
            index_target=round(target, 2),
        )

    def _compute_confidence(
        self, ctx: MarketContext, arm: _Arm, rr: float, is_long: bool,
    ) -> tuple[float, dict]:
        """Lean structural confidence — recorded for calibration, NEVER an S2-style gate.

        Deliberately does NOT reuse S2's `compute_confidence` (the study proved it inverted,
        r −0.224). Three cheap structural factors; rebuild from the new entry's own win/loss
        data once the shadow A/B has outcomes.
        """
        # Pullback depth — distance into the VWAP band: 0 at VWAP → 1 at the outer band.
        depth = min(1.0, abs(arm.dist_at_arm) / 0.15)
        rr_factor = max(0.0, min(1.0, (rr - 1.0) / 1.0))
        bias_factor = 0.5
        if ctx.intraday_bias is not None and abs(ctx.intraday_bias.score) >= 0.20:
            bias_factor = 0.8 if ((ctx.intraday_bias.score > 0) == is_long) else 0.2
        composite = (depth * 0.40 + bias_factor * 0.30 + rr_factor * 0.30) * 100
        factors = {
            "pullback_depth_factor": round(depth, 3),
            "bias_factor": round(bias_factor, 3),
            "rr_factor": round(rr_factor, 3),
        }
        return round(max(0.0, min(100.0, composite)), 1), factors

    def _build_indicator_snapshot(self, ctx: MarketContext, indicators: dict) -> None:
        """Enrich indicators with cross-cutting context for the UI / analysis."""
        if ctx.previous_day:
            indicators.setdefault("pdh", ctx.previous_day.pdh)
            indicators.setdefault("pdl", ctx.previous_day.pdl)
            indicators["pdc"] = ctx.previous_day.pdc
        if ctx.cpr:
            indicators["cpr_type"] = ctx.cpr.cpr_type.value
        if ctx.india_vix:
            indicators["india_vix"] = ctx.india_vix
        if ctx.intraday_bias:
            indicators["intraday_bias"] = ctx.intraday_bias.components
        if ctx.global_cues:
            indicators["global_score"] = ctx.global_cues.global_score

    def should_exit(
        self,
        ctx: MarketContext,
        entry_price: float,
        stop_loss: float,
        target_price: float | None,
    ) -> ExitSignal | None:
        """Exits handled by trade_monitor (SL/target/trailing/3:25)."""
        return None
