"""Strategy 6: Breakout-Retest Intraday Futures.

Fixes Strategy 5's core weakness — late breakout-chasing into local extremes that
immediately pull back (signal-accuracy study: target-first 18% << 40% random, i.e.
*worse* than a coin flip on R:R). Instead of buying the breakout, this strategy
waits for the **retest**:

    1. ARM     — a completed 5m candle closes beyond a structural level L.
    2. RETEST  — price pulls back on 1m to within proximity of L (tracking the
                 pullback swing extreme), after having actually extended away.
    3. RECLAIM — a 1m candle closes back beyond L *with volume* → FIRE. Entry sits
                 next to a tight, mechanical invalidation (the retest swing).
    Aborts on a 1m slice-through of L, on timeout, or if price never retests.

Levels armed: ORB high/low (first 15m), prev-day high/low, intraday 5m swing pivots.
Hard gates: time-of-day window, with-NIFTY-trend (sign of nifty_day_change_pct),
never-opposing-stock-bias (sign of intraday_bias.score), reclaim-volume.

State is per (symbol × level), in-memory and ephemeral (re-forms on restart — arms
are short-lived; we deliberately skip breakouts we didn't witness rather than chase).
Evaluated on every 1m candle close. SL/target are spot prices; `_resolve_futures`
rescales them to the futures LTP preserving the structural distances.

See docs/strategies/strategy-6-breakout-retest.md for the full specification.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, time as dt_time

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.adr import adr_qualifies, compute_adr
from app.indicators.candle_patterns import Candle
from app.indicators.market_levels import find_pivot_high, find_pivot_low
from app.strategies.base import (
    BaseStrategy,
    ExitSignal,
    MarketContext,
    StrategySignal,
)

logger = logging.getLogger(__name__)


@dataclass
class _Arm:
    """A single armed breakout awaiting its retest+reclaim, per (symbol × level)."""
    level: float
    is_long: bool
    level_type: str                      # ORB | PDH_PDL | SWING
    armed_len_1m: int                    # len(candles_1m) when armed (for timeout)
    breakout_extreme: float              # furthest price reached since arm (in trade dir)
    retest_seen: bool = False
    retest_swing: float | None = None    # pullback extreme (low for long, high for short)
    last_processed_len: int = 0          # len(candles_1m) last processed (per-candle idempotency)


def _parse_time(s: str, default: dt_time) -> dt_time:
    """Parse an 'HH:MM' string to a time; fall back to *default* on bad input."""
    try:
        h, m = s.split(":")
        return dt_time(int(h), int(m))
    except (ValueError, AttributeError):
        return default


def _completed_5m(candles_1m: list[Candle]) -> list[Candle]:
    """Aggregate the 1m series into *completed* 5m candles (drops a partial tail).

    Relies on the 1m series starting at 09:15 (a 5m boundary), so blocks of 5 align
    to 09:15-09:20, 09:20-09:25, … — giving true 5m-close confirmation and stable
    transition detection (the list only grows when a fresh 5m bar completes).
    """
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


class BreakoutRetestStrategy(BaseStrategy):
    """Break → retest → reclaim entry on stock futures (see module docstring)."""

    name = StrategyName.BREAKOUT_RETEST
    holding_type = "INTRADAY"
    max_lots = 2

    _watchlist_cache: list[str] | None = None
    _watchlist_cache_ts: float = 0.0
    _WATCHLIST_CACHE_TTL = 60.0

    def __init__(self) -> None:
        # Per-symbol state (ephemeral; resets on process restart).
        self._arms: dict[str, dict[str, _Arm]] = {}
        self._cooldown: dict[str, set[str]] = {}     # level_keys awaiting an inside-close before re-arm
        self._last_5m_len: dict[str, int] = {}       # completed-5m count last seen (new-bar detection)
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

    async def get_symbols(self) -> list[str] | None:
        """Read today's S5 screener watchlist from Redis (shared universe; 60s cache)."""
        now = time.time()
        if self._watchlist_cache is not None and (now - self._watchlist_cache_ts) < self._WATCHLIST_CACHE_TTL:
            return self._watchlist_cache

        from app.core.redis import get_redis
        from app.core.utils import now_ist

        r = get_redis()
        raw = await r.get(f"strat5:watchlist:{now_ist().date()}")
        symbols = [w["symbol"] for w in json.loads(raw)] if raw else None
        self._watchlist_cache = symbols
        self._watchlist_cache_ts = now
        return symbols

    @staticmethod
    def _min_confidence_to_persist() -> float:
        """Global persist threshold from trading_config (mirrors S2/S5)."""
        from app.services.trading_config import get_trading_config_sync
        cfg = get_trading_config_sync()
        return cfg.min_confidence_to_persist if cfg else 30.0

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Arm on 5m breakouts, then fire on a 1m retest-reclaim. One signal max."""
        from app.core.utils import now_ist

        params = ctx.strategy_params or {}
        sym = ctx.symbol
        self._arms.setdefault(sym, {})
        self._cooldown.setdefault(sym, set())

        candles_1m = ctx.candles_1m or []
        if len(candles_1m) < 5:
            return None

        arm_start = _parse_time(params.get("arm_start", "09:30"), dt_time(9, 30))
        arm_cutoff = _parse_time(params.get("arm_cutoff", "13:30"), dt_time(13, 30))
        t = now_ist().time()
        if t < arm_start:
            return None

        completed_5m = _completed_5m(candles_1m)
        if not completed_5m:
            return None

        # 1) Arm on newly-completed 5m breakouts (only inside the arm window).
        if arm_start <= t < arm_cutoff and len(completed_5m) > self._last_5m_len.get(sym, 0):
            levels = self._current_levels(ctx, completed_5m, params)
            self._update_arms(sym, completed_5m, levels, len(candles_1m), params)
            self._last_5m_len[sym] = len(completed_5m)

        # 2) Process arms on the latest 1m (retest / reclaim / abort). No fresh
        #    entries after the cutoff — arms only abort/expire past it.
        return self._process_arms(ctx, params, allow_fire=t < arm_cutoff)

    # ------------------------------------------------------------------
    # Arming
    # ------------------------------------------------------------------

    def _current_levels(
        self, ctx: MarketContext, completed_5m: list[Candle], params: dict
    ) -> list[tuple[str, float, bool, str]]:
        """Return armable levels as (level_key, price, is_long, level_type).

        SWING levels carry their price in the key (a re-formed pivot is a new arm);
        a swing within `retest_proximity` of an ORB/PDH-PDL level is dropped to avoid
        double-arming the same price.
        """
        enabled = set(params.get("enabled_levels", ["ORB", "PDH_PDL", "SWING"]))
        levels: list[tuple[str, float, bool, str]] = []

        if "PDH_PDL" in enabled and ctx.previous_day:
            levels.append(("PDH", ctx.previous_day.pdh, True, "PDH_PDL"))
            levels.append(("PDL", ctx.previous_day.pdl, False, "PDH_PDL"))

        if "ORB" in enabled and len(completed_5m) >= 3:
            orb = completed_5m[:3]            # 09:15-09:30 (1m series starts at 09:15)
            levels.append(("ORB_H", max(c.high for c in orb), True, "ORB"))
            levels.append(("ORB_L", min(c.low for c in orb), False, "ORB"))

        if "SWING" in enabled:
            left = int(params.get("swing_pivot_left", 2))
            right = int(params.get("swing_pivot_right", 2))
            prox = params.get("retest_proximity_pct", 0.15) / 100.0
            fixed = [lv for _, lv, _, _ in levels]
            ph = find_pivot_high(completed_5m, left=left, right=right)
            if ph is not None and not any(abs(ph - f) <= ph * prox for f in fixed):
                levels.append((f"SWING_H@{round(ph, 2)}", ph, True, "SWING"))
            pl = find_pivot_low(completed_5m, left=left, right=right)
            if pl is not None and not any(abs(pl - f) <= pl * prox for f in fixed):
                levels.append((f"SWING_L@{round(pl, 2)}", pl, False, "SWING"))

        return levels

    def _update_arms(
        self,
        sym: str,
        completed_5m: list[Candle],
        levels: list[tuple[str, float, bool, str]],
        cur_1m_len: int,
        params: dict,
    ) -> None:
        """Arm levels broken by the just-completed 5m bar; clear satisfied cooldowns."""
        last_close = completed_5m[-1].close
        ext = params.get("min_breakout_ext_pct", 0.05) / 100.0
        arms = self._arms[sym]
        cooldown = self._cooldown[sym]

        for key, lvl, is_long, ltype in levels:
            if key in arms:
                continue  # already armed
            inside = last_close <= lvl if is_long else last_close >= lvl
            if key in cooldown:
                if inside:
                    cooldown.discard(key)   # price came back inside → re-armable later
                continue
            beyond = last_close > lvl * (1 + ext) if is_long else last_close < lvl * (1 - ext)
            if beyond:
                arms[key] = _Arm(
                    level=lvl,
                    is_long=is_long,
                    level_type=ltype,
                    armed_len_1m=cur_1m_len,
                    breakout_extreme=last_close,
                )
                self._log("ARM", f"{sym}: {ltype} {'long' if is_long else 'short'} armed at {lvl:.2f}")

    # ------------------------------------------------------------------
    # Retest / reclaim / abort  (1m precision)
    # ------------------------------------------------------------------

    def _process_arms(
        self, ctx: MarketContext, params: dict, allow_fire: bool
    ) -> StrategySignal | None:
        """Advance every armed level on the latest 1m candle; fire the first reclaim."""
        sym = ctx.symbol
        candles_1m = ctx.candles_1m or []
        cur_len = len(candles_1m)
        c1 = candles_1m[-1]
        arms = self._arms[sym]
        cooldown = self._cooldown[sym]
        prox = params.get("retest_proximity_pct", 0.15) / 100.0
        ext = params.get("min_breakout_ext_pct", 0.05) / 100.0
        slice_buf = params.get("slice_buffer_pct", 0.20) / 100.0
        reclaim_buf = params.get("reclaim_buffer_pct", 0.0) / 100.0
        max_wait = int(params.get("max_wait_minutes", 30))

        fired: StrategySignal | None = None
        for key in list(arms.keys()):
            arm = arms[key]
            if cur_len <= arm.last_processed_len:
                continue  # no new 1m candle since last processed (idempotent)
            arm.last_processed_len = cur_len
            L = arm.level

            # Timeout — breakout fizzled (ran sideways / never retested in time).
            if cur_len - arm.armed_len_1m > max_wait:
                del arms[key]
                cooldown.add(key)
                self._log("ABORT", f"{sym}: {arm.level_type} arm timed out at {L:.2f}")
                continue

            # Slice-through — a decisive close the wrong side means the level failed.
            sliced = c1.close < L * (1 - slice_buf) if arm.is_long else c1.close > L * (1 + slice_buf)
            if sliced:
                del arms[key]   # price now on the wrong side; only a fresh break re-arms
                self._log("ABORT", f"{sym}: {arm.level_type} sliced through {L:.2f}")
                continue

            # Track how far the breakout extended (gates the retest).
            arm.breakout_extreme = (
                max(arm.breakout_extreme, c1.high) if arm.is_long
                else min(arm.breakout_extreme, c1.low)
            )
            extended = (
                arm.breakout_extreme >= L * (1 + ext) if arm.is_long
                else arm.breakout_extreme <= L * (1 - ext)
            )

            # Retest — price pulls back to within proximity of the level.
            if not arm.retest_seen:
                if not extended:
                    continue
                near = c1.low <= L * (1 + prox) if arm.is_long else c1.high >= L * (1 - prox)
                if near:
                    arm.retest_seen = True
                    arm.retest_swing = c1.low if arm.is_long else c1.high
                continue

            # In a retest — extend the pullback swing, then look for the reclaim.
            arm.retest_swing = (
                min(arm.retest_swing, c1.low) if arm.is_long
                else max(arm.retest_swing, c1.high)
            )
            reclaimed = (
                c1.close > L * (1 + reclaim_buf) and c1.close > c1.open if arm.is_long
                else c1.close < L * (1 - reclaim_buf) and c1.close < c1.open
            )
            if not reclaimed:
                continue

            # One fire attempt per retest: consume the arm regardless of gate outcome.
            del arms[key]
            cooldown.add(key)
            if not allow_fire:
                continue
            signal = self._build_signal(ctx, params, arm, c1)
            if signal is not None and fired is None:
                fired = signal

        return fired

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _build_signal(
        self, ctx: MarketContext, params: dict, arm: _Arm, reclaim: Candle
    ) -> StrategySignal | None:
        """Validate gates, compute tight-SL geometry, and build the signal (or None)."""
        sym = ctx.symbol
        is_long = arm.is_long
        direction = "LONG" if is_long else "SHORT"
        entry = reclaim.close
        L = arm.level

        # --- Base filters ---
        if entry < params.get("min_price", 100.0):
            self._log("SKIP", f"{sym}: price {entry:.1f} below min ({direction})")
            return None
        adr_pct = compute_adr(ctx.candles_daily) if ctx.candles_daily else 0.0
        if ctx.candles_daily and not adr_qualifies(adr_pct, min_adr=params.get("min_adr", 1.5)):
            self._log("SKIP", f"{sym}: ADR {adr_pct:.1f}% below min ({direction})")
            return None

        # --- Reclaim volume confirmation (vs recent 1m average) ---
        vol_ok, vol_ratio = self._reclaim_volume(ctx, params, reclaim)
        if not vol_ok:
            self._log("SKIP", f"{sym}: reclaim vol {vol_ratio:.2f}x < {params.get('reclaim_vol_mult', 1.5)} ({direction})")
            return None

        # --- Regime gates (sign checks with deadbands; never oppose) ---
        if params.get("require_with_nifty_trend", True):
            ndc = params.get("_nifty_day_change_pct")
            dead = params.get("nifty_flat_deadband_pct", 0.10)
            if ndc is not None and abs(ndc) >= dead and ((ndc > 0) != is_long):
                self._log("GATE", f"{sym}: counter-NIFTY-trend ({ndc:+.2f}%) blocks {direction}")
                return None
        if params.get("block_opposing_stock_bias", True) and ctx.intraday_bias is not None:
            score = ctx.intraday_bias.score
            dead = params.get("stock_bias_deadband", 0.15)
            if abs(score) >= dead and ((score > 0) != is_long):
                self._log("GATE", f"{sym}: stock bias {score:+.2f} opposes {direction}")
                return None

        # --- Tight SL just past the retest swing; target by R:R ---
        swing = arm.retest_swing if arm.retest_swing is not None else (reclaim.low if is_long else reclaim.high)
        atr = ctx.atr_5m
        sl_buf = max(entry * params.get("sl_swing_buffer_pct", 0.15) / 100.0,
                     (atr * params.get("sl_atr_mult", 0.3)) if atr else 0.0)
        stop_loss = swing - sl_buf if is_long else swing + sl_buf

        # Whipsaw floor / sanity / width cap on the risk distance.
        risk = entry - stop_loss if is_long else stop_loss - entry
        if risk <= 0:
            self._log("SKIP", f"{sym}: degenerate SL {stop_loss:.2f} vs entry {entry:.2f} ({direction})")
            return None
        min_risk = entry * params.get("min_risk_pct", 0.10) / 100.0
        if risk < min_risk:                       # too tight → widen to floor (anti-whipsaw)
            risk = min_risk
            stop_loss = entry - risk if is_long else entry + risk
        if risk > entry * params.get("max_risk_pct", 1.5) / 100.0:
            self._log("SKIP", f"{sym}: risk {risk:.2f} too wide ({direction})")
            return None

        rr = params.get("rr_multiplier", 1.8)
        target = entry + rr * risk if is_long else entry - rr * risk
        if rr < params.get("min_rr", 1.5):
            self._log("SKIP", f"{sym}: R:R {rr:.2f} below min ({direction})")
            return None

        # --- Confidence (lean, structural) + persist gate ---
        indicators: dict = {
            "setup_type": f"{arm.level_type}_RETEST",
            "level": round(L, 2),
            "level_type": arm.level_type,
            "entry_style": "RETEST",
            "retest_swing": round(swing, 2),
            "breakout_extreme": round(arm.breakout_extreme, 2),
            "reclaim_vol_ratio": round(vol_ratio, 2),
            "rr": round(rr, 2),
            "adr_pct": round(adr_pct, 2) if adr_pct else None,
        }
        confidence = self._compute_confidence(params, arm.level_type, vol_ratio, rr, is_long, indicators)
        if confidence < self._min_confidence_to_persist():
            self._log("GATE", f"{sym} {arm.level_type}_RETEST: confidence {confidence:.1f} < persist threshold")
            return None

        self._build_indicator_snapshot(ctx, params, indicators)
        self._log("SIGNAL", f"{sym}: {arm.level_type} retest {direction} entry {entry:.2f} SL {stop_loss:.2f} ({confidence:.0f})")

        return StrategySignal(
            strategy_name=self.name,
            symbol=sym,
            signal_type=SignalType.BUY_FUT if is_long else SignalType.SELL_FUT,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date.today(),
            entry_price=entry,
            stop_loss=stop_loss,
            target_price=target,
            confidence=confidence,
            reason=(
                f"{arm.level_type} retest {'reclaim above' if is_long else 'reclaim below'} "
                f"{L:.2f} (swing {swing:.2f}, {vol_ratio:.1f}x vol)"
            ),
            indicators=indicators,
        )

    def _reclaim_volume(
        self, ctx: MarketContext, params: dict, reclaim: Candle
    ) -> tuple[bool, float]:
        """Reclaim 1m candle volume vs the average of the recent 1m candles."""
        candles_1m = ctx.candles_1m or []
        lookback = int(params.get("reclaim_vol_lookback", 20))
        prior = candles_1m[-(lookback + 1):-1] if len(candles_1m) > 1 else []
        vols = [c.volume for c in prior if c.volume > 0]
        if not vols:
            return True, 0.0   # no baseline (illiquid/early) — don't block on volume
        avg = sum(vols) / len(vols)
        ratio = reclaim.volume / avg if avg > 0 else 0.0
        return ratio >= params.get("reclaim_vol_mult", 1.5), ratio

    def _compute_confidence(
        self, params: dict, level_type: str, vol_ratio: float, rr: float,
        is_long: bool, indicators: dict,
    ) -> float:
        """Lean 4-factor composite — the noisy S5 factors are deliberately omitted.

        setup_quality (level type) 0.30, reclaim_volume 0.30, oi_alignment 0.20,
        rr_quality 0.20. Injects a `confidence_factors` dict for the UI / AI overlay.
        """
        setup_factor = {"ORB": 0.8, "PDH_PDL": 0.7, "SWING": 0.6}.get(level_type, 0.6)

        mult = params.get("reclaim_vol_mult", 1.5)
        vol_factor = min(1.0, vol_ratio / (mult * 2)) if mult > 0 else 0.5

        rr_factor = max(0.0, min(1.0, (rr - 1.0) / 1.0))   # rr 2.0 → 1.0

        # OI alignment — the one weakly-real informational factor from the study.
        oi_dir = params.get("_oi_direction")
        oi_factor = 0.5
        if oi_dir == "long_buildup":
            oi_factor = 0.85 if is_long else 0.15
        elif oi_dir == "short_buildup":
            oi_factor = 0.85 if not is_long else 0.15
        elif oi_dir == "short_covering":
            oi_factor = 0.6 if is_long else 0.4
        elif oi_dir == "long_unwinding":
            oi_factor = 0.4 if is_long else 0.6

        composite = (
            setup_factor * 0.30
            + vol_factor * 0.30
            + oi_factor * 0.20
            + rr_factor * 0.20
        ) * 100

        indicators["confidence_factors"] = {
            "setup_factor": round(setup_factor, 3),
            "reclaim_vol_factor": round(vol_factor, 3),
            "oi_factor": round(oi_factor, 3),
            "rr_factor": round(rr_factor, 3),
        }
        return round(max(0, min(100, composite)), 1)

    def _build_indicator_snapshot(
        self, ctx: MarketContext, params: dict, indicators: dict
    ) -> None:
        """Enrich indicators with cross-cutting context for the AI overlay / analysis."""
        indicators["price"] = ctx.current_price
        if ctx.vwap:
            indicators.setdefault("vwap", round(ctx.vwap.vwap, 2))
        if ctx.previous_day:
            indicators.setdefault("pdh", ctx.previous_day.pdh)
            indicators.setdefault("pdl", ctx.previous_day.pdl)
            indicators["pdc"] = ctx.previous_day.pdc
        if ctx.india_vix:
            indicators["india_vix"] = ctx.india_vix
        if ctx.intraday_bias:
            indicators["intraday_bias"] = ctx.intraday_bias.components
        ndc = params.get("_nifty_day_change_pct")
        if ndc is not None:
            indicators["nifty_day_change_pct"] = ndc
        oi_dir = params.get("_oi_direction")
        if oi_dir:
            indicators["fut_oi_direction"] = oi_dir
            indicators["fut_oi_change_pct"] = params.get("_oi_change_pct")
        trend_score = params.get("_stock_trend_score")
        if trend_score is not None:
            indicators["stock_trend_score"] = trend_score
            indicators["stock_trend_strength"] = params.get("_stock_trend_strength")
        if params.get("_screener_score"):
            indicators["screener_score"] = params.get("_screener_score")

    def should_exit(
        self,
        ctx: MarketContext,
        entry_price: float,
        stop_loss: float,
        target_price: float | None,
    ) -> ExitSignal | None:
        """Exits handled by trade_monitor (SL/target/trailing/3:25 + thesis-invalidation)."""
        return None
