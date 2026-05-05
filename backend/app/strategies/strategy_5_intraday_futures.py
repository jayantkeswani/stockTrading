"""Strategy 5: Intraday Stock Futures — AI-agent-driven ORB breakout.

Autonomous daily workflow:
  Pre-market: Morning briefing (LLM) → screener (quant + news + LLM) → watchlist
  9:15-9:30:  ORB formation (track high/low of first 15 minutes)
  9:30-14:45: Signal generation on ORB breakouts with volume + VWAP confirmation
  14:45+:     No new entries; existing positions managed by trade_monitor

Phase state machine governs what the strategy does at each time of day.
Dynamic symbol selection via Redis watchlist (populated by morning_screener).
See docs/strategies/strategy-5-intraday-futures.md for full specification.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, time as dt_time

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.adr import adr_qualifies, compute_adr
from app.indicators.candle_patterns import (
    average_volume,
    is_bearish_reversal,
    is_bullish_reversal,
)
from app.indicators.gap_analysis import detect_gap, is_gap_continuation
from app.indicators.market_levels import find_swing_high, find_swing_low
from app.indicators.rvol import compute_rvol, deserialize_profile
from app.indicators.vwap import is_pullback_to_vwap
from app.strategies.base import (
    BaseStrategy,
    ExitSignal,
    MarketContext,
    StrategySignal,
)

logger = logging.getLogger(__name__)

# Phase boundaries (IST)
_PHASE_SCHEDULE: list[tuple[dt_time, str]] = [
    (dt_time(9, 15), "ORB_FORMING"),
    (dt_time(9, 30), "MORNING_ACTIVE"),
    (dt_time(11, 30), "CAUTION_ZONE"),
    (dt_time(13, 0), "AFTERNOON"),
    (dt_time(14, 45), "CLOSING"),
    (dt_time(15, 15), "DONE"),
]


def get_current_phase(as_of: datetime | None = None) -> str:
    """Determine the current trading phase from IST time."""
    from app.core.utils import now_ist

    now = as_of or now_ist()
    t = now.time()

    if t < dt_time(9, 15):
        return "PRE_MARKET"

    current_phase = "DONE"
    for boundary, phase in _PHASE_SCHEDULE:
        if t < boundary:
            break
        current_phase = phase

    return current_phase


class IntradayFuturesStrategy(BaseStrategy):
    name = StrategyName.INTRADAY_FUTURES
    holding_type = "INTRADAY"
    max_lots = 2

    _watchlist_cache: list[str] | None = None
    _watchlist_cache_ts: float = 0.0
    _WATCHLIST_CACHE_TTL = 60.0
    _pending_logs: list[tuple[str, str]] = []
    _pending_confirmations: dict[str, dict] = {}
    _last_logged_phase: str | None = None
    _pending_phase: str | None = None

    def drain_pending_logs(self) -> list[tuple[str, str]]:
        """Return and clear pending log entries [(category, message), ...]."""
        logs = self._pending_logs
        self._pending_logs = []
        return logs

    async def get_symbols(self) -> list[str] | None:
        """Read today's watchlist from Redis. Cached in-memory for 60s."""
        now = time.time()
        if self._watchlist_cache is not None and (now - self._watchlist_cache_ts) < self._WATCHLIST_CACHE_TTL:
            return self._watchlist_cache

        from app.core.redis import get_redis
        from app.core.utils import now_ist

        r = get_redis()
        today = now_ist().date()
        raw = await r.get(f"strat5:watchlist:{today}")
        if not raw:
            self._watchlist_cache = None
            self._watchlist_cache_ts = now
            return None

        watchlist = json.loads(raw)
        symbols = [w["symbol"] for w in watchlist]
        self._watchlist_cache = symbols
        self._watchlist_cache_ts = now
        return symbols

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Phase-based dispatch hub for all sub-setups."""
        phase = get_current_phase()
        params = ctx.strategy_params or {}

        if phase in ("PRE_MARKET", "CLOSING", "DONE"):
            return None

        # Phase transition logging
        if phase != self._last_logged_phase:
            self._pending_logs.append(("PHASE", f"Entering {phase}"))
            self._last_logged_phase = phase
            self._pending_phase = phase

        if phase == "ORB_FORMING":
            self._update_orb_levels(ctx)
            return None

        # Check pending caution zone confirmations first
        confirmed = self._check_pending_confirmations(ctx, phase)
        if confirmed:
            return confirmed

        # Dispatch sub-setups in priority order for current phase
        enabled = set(params.get("enabled_setups", ["ORB"]))
        for setup in self._get_dispatch_order(phase):
            if setup not in enabled:
                continue
            signal = self._dispatch_setup(setup, ctx, phase, params)
            if signal is None:
                continue
            # Caution zone: store pending instead of firing immediately
            if phase == "CAUTION_ZONE" and setup in ("PDH_PDL", "VWAP_BOUNCE"):
                self._store_pending_confirmation(ctx, signal, setup)
                self._pending_logs.append(("PENDING", f"{ctx.symbol}: {setup} signal stored for confirmation"))
                return None
            return signal

        return None

    def _get_dispatch_order(self, phase: str) -> list[str]:
        if phase == "MORNING_ACTIVE":
            return ["ORB", "GAP_CONTINUATION", "PDH_PDL", "VWAP_BOUNCE"]
        if phase == "CAUTION_ZONE":
            return ["PDH_PDL", "VWAP_BOUNCE"]
        if phase == "AFTERNOON":
            return ["VWAP_BOUNCE", "PDH_PDL"]
        return []

    def _dispatch_setup(
        self, setup: str, ctx: MarketContext, phase: str, params: dict
    ) -> StrategySignal | None:
        dispatch = {
            "ORB": self._check_orb_breakout,
            "VWAP_BOUNCE": self._check_vwap_bounce,
            "PDH_PDL": self._check_pdh_pdl_breakout,
            "GAP_CONTINUATION": self._check_gap_continuation,
        }
        handler = dispatch.get(setup)
        if handler is None:
            return None
        return handler(ctx, phase, params)

    def _store_pending_confirmation(
        self, ctx: MarketContext, signal: StrategySignal, setup_type: str
    ) -> None:
        self._pending_confirmations[ctx.symbol] = {
            "setup_type": setup_type,
            "signal": signal,
            "direction": "LONG" if signal.signal_type == SignalType.BUY_FUT else "SHORT",
            "breakout_price": ctx.current_price,
            "stored_at": time.time(),
            "candle_count": len(ctx.candles_5m),
        }

    def _check_pending_confirmations(
        self, ctx: MarketContext, phase: str
    ) -> StrategySignal | None:
        pending = self._pending_confirmations.get(ctx.symbol)
        if not pending:
            return None

        # Staleness: discard after 10 minutes
        if time.time() - pending["stored_at"] > 600:
            del self._pending_confirmations[ctx.symbol]
            self._pending_logs.append(("SKIP", f"{ctx.symbol}: pending {pending['setup_type']} expired"))
            return None

        # Phase changed away from caution zone: discard
        if phase != "CAUTION_ZONE":
            del self._pending_confirmations[ctx.symbol]
            return None

        # Need a new candle since the signal was stored
        if len(ctx.candles_5m) <= pending["candle_count"]:
            return None

        # Check if price held the breakout direction
        latest_close = ctx.candles_5m[-1].close
        held = (
            (pending["direction"] == "LONG" and latest_close >= pending["breakout_price"])
            or (pending["direction"] == "SHORT" and latest_close <= pending["breakout_price"])
        )

        del self._pending_confirmations[ctx.symbol]
        if held:
            return pending["signal"]

        self._pending_logs.append(("SKIP", f"{ctx.symbol}: pending {pending['setup_type']} not confirmed"))
        return None

    def _update_orb_levels(self, ctx: MarketContext) -> None:
        """Track ORB high/low during 9:15-9:30 formation window.

        Stores in-memory and queues Redis persistence via _pending_orb_writes.
        """
        if not ctx.candles_5m:
            return

        orb_candles = ctx.candles_5m[-3:] if len(ctx.candles_5m) >= 3 else ctx.candles_5m
        orb_high = max(c.high for c in orb_candles)
        orb_low = min(c.low for c in orb_candles)

        if not hasattr(self, "_orb_levels"):
            self._orb_levels: dict[str, dict] = {}
        self._orb_levels[ctx.symbol] = {"high": orb_high, "low": orb_low}

        if not hasattr(self, "_pending_orb_writes"):
            self._pending_orb_writes: dict[str, dict] = {}
        self._pending_orb_writes[ctx.symbol] = {"high": orb_high, "low": orb_low}

    def drain_pending_orb_writes(self) -> dict[str, dict]:
        """Return and clear pending ORB level writes for Redis persistence."""
        writes = getattr(self, "_pending_orb_writes", {})
        self._pending_orb_writes = {}
        return writes

    def get_pending_phase(self) -> str | None:
        """Return and clear pending phase for Redis persistence."""
        phase = self._pending_phase
        self._pending_phase = None
        return phase

    def load_orb_from_redis(self, symbol: str, orb_data: dict) -> None:
        """Restore ORB levels from Redis (called by strategy_runner)."""
        if not hasattr(self, "_orb_levels"):
            self._orb_levels = {}
        if symbol not in self._orb_levels:
            self._orb_levels[symbol] = orb_data

    def _skip(self, symbol: str, reason: str) -> None:
        self._pending_logs.append(("SKIP", f"{symbol}: {reason}"))

    def _check_stock_trend_filter(
        self, symbol: str, is_long: bool, params: dict, risk_warnings: list[str],
    ) -> bool:
        """Check stock trend direction. Returns False if signal should be blocked."""
        stock_trend_strength = params.get("_stock_trend_strength")
        stock_bias = params.get("_stock_bias")
        if not stock_trend_strength or not stock_bias:
            return True

        direction = "LONG" if is_long else "SHORT"
        opposing = (is_long and stock_bias == "BEARISH") or (
            not is_long and stock_bias == "BULLISH"
        )
        if not opposing:
            return True

        if stock_trend_strength == "STRONG":
            self._skip(symbol, f"{direction} blocked by STRONG {stock_bias} stock trend")
            return False
        if stock_trend_strength == "MODERATE":
            risk_warnings.append(f"{direction} against MODERATE {stock_bias} stock trend")
        return True

    def _check_orb_breakout(
        self, ctx: MarketContext, phase: str, params: dict
    ) -> StrategySignal | None:
        """Check for ORB breakout with volume + VWAP confirmation."""
        from app.core.utils import now_ist

        t = now_ist().time()
        if t < dt_time(9, 30) or t >= dt_time(11, 0):
            return None

        orb = getattr(self, "_orb_levels", {}).get(ctx.symbol)
        if not orb:
            return None

        orb_high = orb["high"]
        orb_low = orb["low"]

        # Use confirmed 5-minute candle close for breakout detection — not the
        # live tick.  The ORB itself is defined on 5-min candles, so the breakout
        # should be confirmed on the same timeframe to avoid false breakouts from
        # intra-candle spikes.
        if not ctx.candles_5m:
            return None
        last_5m = ctx.candles_5m[-1]
        price = last_5m.close

        # Need a clear breakout
        if orb_low <= price <= orb_high:
            return None

        is_long = price > orb_high
        signal_type = SignalType.BUY_FUT if is_long else SignalType.SELL_FUT
        direction = "LONG" if is_long else "SHORT"

        # ORB range validation — reject noise (too narrow) and oversize risk (too wide)
        orb_range = orb_high - orb_low
        orb_range_pct = (orb_range / price) * 100 if price > 0 else 0
        min_range = params.get("min_orb_range_pct", 0.4)
        max_range = params.get("max_orb_range_pct", 2.0)
        if orb_range_pct < min_range:
            self._skip(ctx.symbol, f"ORB range {orb_range_pct:.2f}% < min {min_range}% ({direction})")
            return None
        if orb_range_pct > max_range:
            self._skip(ctx.symbol, f"ORB range {orb_range_pct:.2f}% > max {max_range}% ({direction})")
            return None

        # ADR filter
        if ctx.candles_daily:
            adr_pct = compute_adr(ctx.candles_daily)
            if not adr_qualifies(adr_pct, min_adr=params.get("min_adr", 1.5)):
                self._skip(ctx.symbol, f"ADR {adr_pct:.1f}% below min ({direction})")
                return None
        else:
            adr_pct = 0.0

        # RVOL filter
        rvol = self._get_current_rvol(ctx)
        rvol_threshold = params.get("rvol_threshold", 1.5)
        if phase == "CAUTION_ZONE":
            rvol_threshold = params.get("rvol_caution_zone_threshold", 2.5)
        if rvol is not None and rvol < rvol_threshold:
            self._skip(ctx.symbol, f"RVOL {rvol:.2f} < {rvol_threshold} ({direction})")
            return None

        # Price filter
        if price < 100:
            self._skip(ctx.symbol, f"price {price:.1f} < 100 ({direction})")
            return None

        # Volume confirmation on breakout candle
        if ctx.candles_5m:
            current_vol = ctx.candles_5m[-1].volume
            if ctx.volume_avg_20d and ctx.volume_avg_20d > 0:
                vol_ratio = current_vol / (ctx.volume_avg_20d / 75)
                if vol_ratio < 1.2:
                    self._skip(ctx.symbol, f"vol ratio {vol_ratio:.2f} < 1.2 ({direction})")
                    return None

        # VWAP filter: longs should be above VWAP, shorts below
        if ctx.vwap:
            vwap_price = ctx.vwap.vwap
            if is_long and price < vwap_price:
                self._skip(ctx.symbol, f"price {price:.1f} below VWAP {vwap_price:.1f} (LONG)")
                return None
            if not is_long and price > vwap_price:
                self._skip(ctx.symbol, f"price {price:.1f} above VWAP {vwap_price:.1f} (SHORT)")
                return None

        # Nifty intraday bias alignment gate (STRONG opposing blocks signal)
        nifty_bias = params.get("_nifty_bias")
        if nifty_bias is not None:
            strength = getattr(nifty_bias, "strength", None)
            bias_dir = str(getattr(nifty_bias, "bias", ""))
            if strength == "STRONG":
                if is_long and bias_dir == "BEARISH":
                    self._skip(ctx.symbol, f"Nifty STRONG BEARISH blocks LONG")
                    return None
                if not is_long and bias_dir == "BULLISH":
                    self._skip(ctx.symbol, f"Nifty STRONG BULLISH blocks SHORT")
                    return None

        # Stock trend direction filter
        risk_warnings_trend: list[str] = []
        if not self._check_stock_trend_filter(ctx.symbol, is_long, params, risk_warnings_trend):
            return None

        # SL/Target calculation
        if is_long:
            stop_loss = orb_low
            risk = price - stop_loss
            target = price + risk * 1.5
        else:
            stop_loss = orb_high
            risk = stop_loss - price
            target = price - risk * 1.5

        # R:R check (guaranteed 1.5 by construction, but guard against edge cases)
        reward = abs(target - price)
        if risk <= 0 or reward / risk < 1.5:
            self._skip(ctx.symbol, f"R:R insufficient ({direction})")
            return None

        # Cross-position soft checks
        risk_warnings = self._check_cross_position_risks(ctx, params)
        risk_warnings.extend(risk_warnings_trend)

        # Enhanced ORB: breakout also crosses PDH (long) or PDL (short)
        enhanced_orb = False
        if ctx.previous_day:
            if is_long and price > ctx.previous_day.pdh:
                enhanced_orb = True
            elif not is_long and price < ctx.previous_day.pdl:
                enhanced_orb = True

        indicators = {
            "setup_type": "ORB",
            "orb_high": orb_high,
            "orb_low": orb_low,
            "orb_range": round(orb_range, 2),
            "phase": phase,
            "adr_pct": round(adr_pct, 2),
            "rvol": round(rvol, 2) if rvol else None,
            "risk_warnings": risk_warnings,
            "enhanced_orb": enhanced_orb,
        }

        # Position sizing
        lots = self._compute_lots(rvol, params, indicators)
        if ctx.vwap:
            indicators["vwap"] = round(ctx.vwap.vwap, 2)

        breakout_vol = ctx.candles_5m[-1].volume if ctx.candles_5m else None
        confidence = self._compute_confidence(ctx, phase, params, "ORB", rvol, breakout_vol, is_long=is_long, indicators=indicators)

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=signal_type,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date.today(),
            entry_price=price,
            stop_loss=stop_loss,
            target_price=target,
            confidence=confidence,
            reason=f"ORB {'breakout above' if is_long else 'breakdown below'} {orb_high if is_long else orb_low:.2f}",
            indicators=indicators,
            lots=lots,
        )

    def _check_vwap_bounce(
        self, ctx: MarketContext, phase: str, params: dict
    ) -> StrategySignal | None:
        """VWAP Bounce: trend established on one side, pullback to VWAP + reversal candle."""
        from app.core.utils import now_ist

        t = now_ist().time()
        if t < dt_time(10, 0) or t >= dt_time(14, 45):
            return None

        if not ctx.vwap or not ctx.candles_5m or len(ctx.candles_5m) < 6:
            return None

        vwap_price = ctx.vwap.vwap
        # VWAP bounce uses live price for entry and proximity — unlike PDH/PDL breakout,
        # the SL here is anchored to VWAP (not to a breakout level relative to price),
        # so live-tick-vs-candle divergence doesn't produce inverted SL.
        price = ctx.current_price

        # Trend check: last 6 candle closes consistently on one side of VWAP
        recent_6 = ctx.candles_5m[-6:]
        all_above = all(c.close > vwap_price for c in recent_6)
        all_below = all(c.close < vwap_price for c in recent_6)

        if not all_above and not all_below:
            return None

        # Pullback: live price within 0.2% of VWAP
        if not is_pullback_to_vwap(price, vwap_price, proximity_pct=0.2):
            return None

        # Reversal candle confirmation
        is_long = all_above
        if is_long and not is_bullish_reversal(ctx.candles_5m):
            return None
        if not is_long and not is_bearish_reversal(ctx.candles_5m):
            return None

        # Volume: reversal candle volume > average
        avg_vol = average_volume(ctx.candles_5m)
        if avg_vol > 0 and ctx.candles_5m[-1].volume <= avg_vol:
            return None

        # Stock trend direction filter
        risk_warnings_trend: list[str] = []
        if not self._check_stock_trend_filter(ctx.symbol, is_long, params, risk_warnings_trend):
            return None

        signal_type = SignalType.BUY_FUT if is_long else SignalType.SELL_FUT

        # SL: below/above VWAP by max(price * 0.3%, 0.5 * ATR)
        atr_buffer = ctx.atr_5m * 0.5 if ctx.atr_5m else price * 0.003
        sl_buffer = max(price * 0.003, atr_buffer)

        if is_long:
            stop_loss = vwap_price - sl_buffer
            swing_target = find_swing_high(ctx.candles_5m)
            risk = price - stop_loss
            target = swing_target if swing_target and swing_target > price else price + risk * 1.5
        else:
            stop_loss = vwap_price + sl_buffer
            swing_target = find_swing_low(ctx.candles_5m)
            risk = stop_loss - price
            target = swing_target if swing_target and swing_target < price else price - risk * 1.5

        # R:R check
        reward = abs(target - price)
        if risk <= 0 or reward / risk < 1.5:
            return None

        rvol = self._get_current_rvol(ctx)

        indicators = {
            "setup_type": "VWAP_BOUNCE",
            "vwap": round(vwap_price, 2),
            "phase": phase,
            "rvol": round(rvol, 2) if rvol else None,
        }
        if risk_warnings_trend:
            indicators["risk_warnings"] = risk_warnings_trend

        lots = self._compute_lots(rvol, params, indicators)
        breakout_vol = ctx.candles_5m[-1].volume if ctx.candles_5m else None
        confidence = self._compute_confidence(ctx, phase, params, "VWAP_BOUNCE", rvol, breakout_vol, is_long=is_long, indicators=indicators)

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=signal_type,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date.today(),
            entry_price=price,
            stop_loss=stop_loss,
            target_price=target,
            confidence=confidence,
            reason=f"VWAP Bounce {'bullish' if is_long else 'bearish'} reversal at {vwap_price:.2f}",
            indicators=indicators,
            lots=lots,
        )

    def _check_pdh_pdl_breakout(
        self, ctx: MarketContext, phase: str, params: dict
    ) -> StrategySignal | None:
        """PDH/PDL Breakout: price breaks previous day high/low with volume + VWAP alignment."""
        from app.core.utils import now_ist

        t = now_ist().time()
        if t < dt_time(9, 30) or t >= dt_time(14, 0):
            return None

        if not ctx.previous_day or not ctx.candles_5m:
            return None

        pdh = ctx.previous_day.pdh
        pdl = ctx.previous_day.pdl
        last_candle = ctx.candles_5m[-1]
        # Use confirmed candle close as the entry price for consistent SL/target computation.
        # ctx.current_price (live tick) can diverge significantly from the breakout candle
        # close, causing SL to land on the wrong side of entry or trivial R:R.
        price = last_candle.close

        # Breakout detection: latest 5m candle close breaks PDH or PDL
        is_long = last_candle.close > pdh
        is_short = last_candle.close < pdl

        if not is_long and not is_short:
            return None

        signal_type = SignalType.BUY_FUT if is_long else SignalType.SELL_FUT

        # Volume: breakout candle > 1.5x average
        avg_vol = average_volume(ctx.candles_5m)
        if avg_vol > 0 and last_candle.volume < avg_vol * 1.5:
            self._skip(ctx.symbol, f"PDH/PDL vol {last_candle.volume} < 1.5x avg {avg_vol:.0f}")
            return None

        # VWAP alignment: longs above, shorts below
        if ctx.vwap:
            vwap_price = ctx.vwap.vwap
            if is_long and price < vwap_price:
                self._skip(ctx.symbol, f"PDH breakout but price below VWAP")
                return None
            if is_short and price > vwap_price:
                self._skip(ctx.symbol, f"PDL breakdown but price above VWAP")
                return None

        # Stock trend direction filter
        risk_warnings_trend: list[str] = []
        if not self._check_stock_trend_filter(ctx.symbol, is_long, params, risk_warnings_trend):
            return None

        # SL: below breakout level by max(price * 0.5%, 0.5 * ATR)
        atr_buffer = ctx.atr_5m * 0.5 if ctx.atr_5m else price * 0.005
        sl_buffer = max(price * 0.005, atr_buffer)

        # Target: measured move = PDH - PDL projected from breakout
        measured_move = pdh - pdl

        if is_long:
            stop_loss = pdh - sl_buffer
            target = price + measured_move
            risk = price - stop_loss
        else:
            stop_loss = pdl + sl_buffer
            target = price - measured_move
            risk = stop_loss - price

        # Sanity: SL must be on the correct side of entry price.
        # Guards against edge cases where pdh/pdl proximity produces an inverted SL.
        if is_long and stop_loss >= price:
            self._skip(ctx.symbol, f"PDH/PDL SL sanity fail: SL {stop_loss:.2f} >= entry {price:.2f}")
            return None
        if is_short and stop_loss <= price:
            self._skip(ctx.symbol, f"PDH/PDL SL sanity fail: SL {stop_loss:.2f} <= entry {price:.2f}")
            return None

        # Fallback target: if measured move gives R:R < 1.5, use risk × 1.5.
        # SHORT signals anchored to pdl + sl_buffer often have wide risk vs a narrow
        # measured move, so the fallback ensures the signal isn't silently dropped.
        if risk > 0 and abs(target - price) / risk < 1.5:
            target = price + risk * 1.5 if is_long else price - risk * 1.5

        # R:R check
        reward = abs(target - price)
        if risk <= 0 or reward / risk < 1.5:
            return None

        rvol = self._get_current_rvol(ctx)

        indicators = {
            "setup_type": "PDH_PDL",
            "pdh": round(pdh, 2),
            "pdl": round(pdl, 2),
            "phase": phase,
            "rvol": round(rvol, 2) if rvol else None,
        }
        if ctx.vwap:
            indicators["vwap"] = round(ctx.vwap.vwap, 2)
        if risk_warnings_trend:
            indicators["risk_warnings"] = risk_warnings_trend

        lots = self._compute_lots(rvol, params, indicators)
        breakout_vol = ctx.candles_5m[-1].volume if ctx.candles_5m else None
        confidence = self._compute_confidence(ctx, phase, params, "PDH_PDL", rvol, breakout_vol, is_long=is_long, indicators=indicators)

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=signal_type,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date.today(),
            entry_price=price,
            stop_loss=stop_loss,
            target_price=target,
            confidence=confidence,
            reason=f"{'PDH' if is_long else 'PDL'} {'breakout' if is_long else 'breakdown'} at {pdh if is_long else pdl:.2f}",
            indicators=indicators,
            lots=lots,
        )

    def _check_gap_continuation(
        self, ctx: MarketContext, phase: str, params: dict
    ) -> StrategySignal | None:
        """Gap Continuation: opening gap holds and price continues in gap direction."""
        from app.core.utils import now_ist

        t = now_ist().time()
        if t < dt_time(9, 30) or t >= dt_time(11, 0):
            return None

        if not ctx.previous_day or ctx.today_open is None or not ctx.candles_5m:
            return None

        # Gap detection
        gap = detect_gap(ctx.today_open, ctx.previous_day.pdc)
        if not gap:
            return None

        # Continuation check: needs 4+ candles (3 formation + 1 confirmation)
        if not is_gap_continuation(ctx.candles_5m, gap["direction"], gap["gap_level"]):
            return None

        # Volume: opening 15-min volume > 2x expected
        if len(ctx.candles_5m) >= 3 and ctx.volume_avg_20d and ctx.volume_avg_20d > 0:
            opening_vol = sum(c.volume for c in ctx.candles_5m[:3])
            expected_opening_vol = (ctx.volume_avg_20d / 75) * 3
            if opening_vol < expected_opening_vol * 2:
                self._skip(ctx.symbol, f"Gap opening vol {opening_vol} < 2x expected {expected_opening_vol:.0f}")
                return None

        is_long = gap["direction"] == "UP"

        # Stock trend direction filter
        risk_warnings_trend: list[str] = []
        if not self._check_stock_trend_filter(ctx.symbol, is_long, params, risk_warnings_trend):
            return None

        signal_type = SignalType.BUY_FUT if is_long else SignalType.SELL_FUT
        # Use confirmed candle close for consistent SL/target/entry computation.
        price = ctx.candles_5m[-1].close

        # SL: beyond gap fill level (PDC) with buffer
        atr_buffer = ctx.atr_5m * 0.5 if ctx.atr_5m else price * 0.003
        sl_buffer = max(price * 0.003, atr_buffer)

        gap_size = abs(ctx.today_open - ctx.previous_day.pdc)

        if is_long:
            stop_loss = ctx.previous_day.pdc - sl_buffer
            target = price + gap_size
            risk = price - stop_loss
        else:
            stop_loss = ctx.previous_day.pdc + sl_buffer
            target = price - gap_size
            risk = stop_loss - price

        # Fallback target if gap size is small
        if risk > 0 and abs(target - price) / risk < 1.5:
            if is_long:
                target = price + risk * 1.5
            else:
                target = price - risk * 1.5

        # R:R check
        reward = abs(target - price)
        if risk <= 0 or reward / risk < 1.5:
            return None

        rvol = self._get_current_rvol(ctx)

        indicators = {
            "setup_type": "GAP_CONTINUATION",
            "gap_direction": gap["direction"],
            "gap_pct": round(gap["gap_pct"], 2),
            "gap_level": round(gap["gap_level"], 2),
            "phase": phase,
            "rvol": round(rvol, 2) if rvol else None,
        }
        if risk_warnings_trend:
            indicators["risk_warnings"] = risk_warnings_trend

        lots = self._compute_lots(rvol, params, indicators)
        breakout_vol = ctx.candles_5m[-1].volume if ctx.candles_5m else None
        confidence = self._compute_confidence(ctx, phase, params, "GAP_CONTINUATION", rvol, breakout_vol, is_long=is_long, indicators=indicators)

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=signal_type,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,
            expiry_date=date.today(),
            entry_price=price,
            stop_loss=stop_loss,
            target_price=target,
            confidence=confidence,
            reason=f"Gap {gap['direction']} continuation ({gap['gap_pct']:.1f}%) from {gap['gap_level']:.2f}",
            indicators=indicators,
            lots=lots,
        )

    def _get_current_rvol(self, ctx: MarketContext) -> float | None:
        """Compute current RVOL from cached baseline profile."""
        # RVOL profile would be loaded from Redis in a real scenario.
        # For evaluate(), we check if it's available in the context indicators.
        if not ctx.candles_5m:
            return None

        current_vol = ctx.candles_5m[-1].volume
        bucket_idx = len(ctx.candles_5m) - 1

        # Profile would come from Redis strat5:rvol_baseline:{symbol}
        # Accessed synchronously from strategy_params or ctx
        params = ctx.strategy_params or {}
        rvol_profile = params.get("_rvol_profile")
        if not rvol_profile:
            return None

        if isinstance(rvol_profile, str):
            rvol_profile = deserialize_profile(rvol_profile)

        bucket_avg = rvol_profile.get(str(bucket_idx), 0.0)
        return compute_rvol(current_vol, bucket_avg)

    def _check_cross_position_risks(self, ctx: MarketContext, params: dict) -> list[str]:
        """Soft enforcement: flag but don't suppress signals."""
        warnings: list[str] = []
        # Actual cross-position checks require DB queries done in strategy_runner.
        # Here we flag based on params thresholds.
        max_positions = params.get("max_simultaneous_positions", 3)
        max_trades = params.get("max_trades_per_day", 5)

        # These would be populated by strategy_runner before evaluate()
        active_positions = params.get("_active_position_count", 0)
        daily_trades = params.get("_daily_trade_count", 0)

        if active_positions >= max_positions:
            warnings.append(f"At max positions ({max_positions})")
        if daily_trades >= max_trades:
            warnings.append(f"At max daily trades ({max_trades})")

        return warnings

    def _compute_lots(
        self, rvol: float | None, params: dict, indicators: dict | None = None,
    ) -> int:
        """Rule-based lot sizing: 1 lot default, 2 if ALL conviction criteria met.

        2 lots requires ALL of:
        1. RVOL >= 3.0
        2. STRONG Nifty bias alignment
        3. Screener score > 70
        4. Enhanced ORB (ORB setup only; other setups skip this condition)
        5. Briefing approach == "aggressive"

        VIX cap: India VIX >= 18 → always 1 lot.
        Capped by morning briefing's max_lots_recommendation.
        """
        india_vix = params.get("_india_vix")
        if india_vix is not None and india_vix >= 18:
            return 1

        briefing_cap = params.get("_briefing_max_lots", 2)

        rvol_ok = rvol is not None and rvol >= 3.0
        nifty_bias = params.get("_nifty_bias")
        bias_ok = nifty_bias is not None and getattr(nifty_bias, "strength", None) == "STRONG"
        score_ok = params.get("_screener_score", 0) > 70
        briefing_ok = params.get("_briefing_approach") == "aggressive"
        trend_ok = params.get("_stock_trend_strength") in ("STRONG", "MODERATE")

        setup_type = (indicators or {}).get("setup_type", "")
        if setup_type == "ORB":
            enhanced_ok = bool((indicators or {}).get("enhanced_orb"))
        else:
            enhanced_ok = True  # non-ORB setups skip this condition

        if rvol_ok and bias_ok and score_ok and briefing_ok and enhanced_ok and trend_ok:
            return min(2, briefing_cap)
        return 1

    def _compute_confidence(
        self,
        ctx: MarketContext,
        phase: str,
        params: dict,
        setup_type: str,
        rvol: float | None,
        breakout_vol: int | None = None,
        is_long: bool = True,
        indicators: dict | None = None,
    ) -> float:
        """Multi-factor confidence composite for Strategy 5 signals.

        Returns a score clamped to [0, 100].  When *indicators* is passed,
        injects a ``confidence_factors`` dict so the frontend can render
        per-factor bar charts.
        """
        threshold = params.get("rvol_threshold", 1.5)

        # 1. Volume quality (0.15) — breakout candle vs avg
        vol_factor = 0.2
        if breakout_vol and ctx.candles_5m:
            avg_vol = average_volume(ctx.candles_5m)
            if avg_vol > 0:
                vol_factor = min(1.0, breakout_vol / (avg_vol * 2))

        # 2. RVOL strength (0.15)
        rvol_factor = 0.0
        if rvol is not None and threshold > 0:
            rvol_factor = min(1.0, (rvol - threshold) / threshold)
            rvol_factor = max(0.0, rvol_factor)

        # 3. Nifty bias alignment (0.12) — direction-aware, same pattern as
        #    Strategy 2's confidence.py bias_alignment factor.
        nifty_bias = params.get("_nifty_bias")
        if nifty_bias is not None:
            score = getattr(nifty_bias, "score", 0.0)
            alignment = score if is_long else -score
            bias_factor = (alignment + 1.0) / 2.0
        else:
            bias_factor = 0.0

        # 4. Phase timing (0.12)
        phase_factor = {
            "MORNING_ACTIVE": 1.0,
            "AFTERNOON": 0.7,
            "CAUTION_ZONE": 0.4,
        }.get(phase, 0.3)

        # 5. Setup quality (0.14)
        setup_factor = {
            "ORB": 0.8,
            "PDH_PDL": 0.7,
            "VWAP_BOUNCE": 0.7,
            "GAP_CONTINUATION": 0.6,
        }.get(setup_type, 0.5)
        # Enhanced ORB gets full score
        if setup_type == "ORB" and getattr(self, "_orb_levels", {}).get(ctx.symbol):
            orb = self._orb_levels[ctx.symbol]
            if ctx.previous_day:
                price = ctx.candles_5m[-1].close if ctx.candles_5m else 0
                if price > ctx.previous_day.pdh or price < ctx.previous_day.pdl:
                    setup_factor = 1.0

        # 6. Screener rank (0.12)
        screener_score = params.get("_screener_score", 0)
        rank_factor = min(1.0, screener_score / 100) if screener_score > 0 else 0.0

        # 7. Gap alignment (0.10) — signal direction matches stock's gap direction
        gap_direction = params.get("_gap_direction")
        relative_gap = params.get("_relative_gap_pct")
        gap_factor = 0.2
        if gap_direction and relative_gap is not None:
            gap_aligned = (is_long and gap_direction == "UP") or (
                not is_long and gap_direction == "DOWN"
            )
            abs_gap = abs(relative_gap)
            if gap_aligned:
                gap_factor = min(1.0, 0.6 + abs_gap * 0.2)
            else:
                gap_factor = max(0.0, 0.4 - abs_gap * 0.2)

        # 8. Stock trend alignment (0.10)
        trend_score = params.get("_stock_trend_score")
        trend_factor = 0.2
        if trend_score is not None:
            if is_long:
                trend_factor = min(1.0, 0.5 + trend_score)
            else:
                trend_factor = min(1.0, 0.5 - trend_score)
            trend_factor = max(0.0, trend_factor)

        # 9. Intraday FUT OI direction (0.10) — 4-way classification,
        #    direction-aware (same pattern as morning screener OI scoring).
        oi_direction = params.get("_oi_direction")
        oi_change_pct = params.get("_oi_change_pct", 0.0)
        abs_oi = abs(oi_change_pct)
        oi_factor = 0.2  # neutral default (missing data penalty)
        if oi_direction is not None:
            if oi_direction == "long_buildup":
                # New longs entering — bullish for LONG, bearish for SHORT
                raw = min(1.0, 0.6 + abs_oi * 0.02)
                oi_factor = raw if is_long else max(0.0, 1.0 - raw)
            elif oi_direction == "short_buildup":
                # New shorts entering — bearish for LONG, bullish for SHORT
                raw = min(1.0, 0.6 + abs_oi * 0.02)
                oi_factor = raw if not is_long else max(0.0, 1.0 - raw)
            elif oi_direction == "short_covering":
                # Shorts exiting — mildly bullish
                oi_factor = 0.6 if is_long else 0.4
            elif oi_direction == "long_unwinding":
                # Longs exiting — mildly bearish
                oi_factor = 0.4 if is_long else 0.6
            else:
                oi_factor = 0.5  # flat — neutral

        composite = (
            vol_factor * 0.10
            + rvol_factor * 0.15
            + bias_factor * 0.12
            + phase_factor * 0.12
            + setup_factor * 0.14
            + rank_factor * 0.07
            + gap_factor * 0.10
            + trend_factor * 0.10
            + oi_factor * 0.10
        ) * 100

        if indicators is not None:
            indicators["confidence_factors"] = {
                "vol_factor": round(vol_factor, 3),
                "rvol_factor": round(rvol_factor, 3),
                "bias_factor": round(bias_factor, 3),
                "phase_factor": round(phase_factor, 3),
                "setup_factor": round(setup_factor, 3),
                "rank_factor": round(rank_factor, 3),
                "gap_factor": round(gap_factor, 3),
                "trend_factor": round(trend_factor, 3),
                "oi_factor": round(oi_factor, 3),
            }

        return round(max(0, min(100, composite)), 1)

    def should_exit(
        self,
        ctx: MarketContext,
        entry_price: float,
        stop_loss: float,
        target_price: float | None,
    ) -> ExitSignal | None:
        """Exits handled by trade_monitor (SL, target, trailing, 3:15 PM time exit)."""
        return None
