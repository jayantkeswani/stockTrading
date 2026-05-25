"""Intraday bias — composite live directional bias for Strategy 2.

Replaces the yesterday-only hard gate in strategy_2_vwap_pullback.py.
Yesterday's bias is now one weighted input rather than a binary gate,
so a bearish gap-down after a bullish yesterday can still trigger PE signals.

Base weights (at market open 9:15 AM):
--------------------------------------
yesterday_close_position   0.10  Regime hint — gap already reprices this
gap_vs_pdc                 0.15  Pre-open / opening gap direction
move_from_pdc              0.15  (current_price - PDC) / PDC — total move from yesterday's close
vwap_slope                 0.30  Intraday trend: slope over last 30 1m candles
price_vs_vwap              0.10  Which side of VWAP price is on right now
global_overnight           0.10  Dow futures, S&P close, USD/INR overnight cues
candle_momentum            0.10  Net direction of last 5 candle bodies
nifty_bias_score           0.05  NIFTY bias injected for non-NIFTY symbols (optional)

Gap and move_from_pdc normalizers scale by previous day's range (ADR proxy)
so volatile stocks need proportionally larger moves to register full signal.

Time-decay: static factors (yesterday, gap) decay as the session progresses,
shifting weight to dynamic factors (VWAP slope, move_from_pdc, price-vs-VWAP, candle momentum).
By 3:15 PM, yesterday drops from 0.10→0.04 and gap from 0.15→0.06.

Score in [-1, +1]: positive = bullish bias, negative = bearish bias.
Strength: STRONG |score|>=0.50, MODERATE >=0.20, WEAK otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from app.core.enums import DayBias
from app.indicators.candle_patterns import Candle
from app.indicators.global_market import GlobalCues
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult

_IST = None  # Lazy-loaded


def _get_ist():
    global _IST
    if _IST is None:
        from zoneinfo import ZoneInfo
        _IST = ZoneInfo("Asia/Kolkata")
    return _IST


@dataclass
class IntradayBias:
    bias: DayBias
    score: float           # Signed [-1, +1]
    strength: str          # "STRONG" | "MODERATE" | "WEAK"
    components: dict       # Individual factor contributions (for debug/UI)


_STRONG_THRESHOLD = 0.50
_MODERATE_THRESHOLD = 0.20

_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 15)
_SESSION_MINUTES = 360.0  # 9:15 → 15:15

_YESTERDAY_DECAY = 0.6   # 0.10 * (1 - 0.6*1.0) = 0.04 at close
_GAP_DECAY = 0.6          # 0.15 * (1 - 0.6*1.0) = 0.06 at close


def _session_progress(as_of: datetime | None) -> float:
    """Return how far through the trading session we are, from 0.0 (9:15 AM) to 1.0 (3:15 PM)."""
    if as_of is None:
        return 0.0
    ist = _get_ist()
    t = as_of.astimezone(ist).time() if as_of.tzinfo else as_of.time()
    minutes_since_open = (
        (t.hour * 60 + t.minute) - (_MARKET_OPEN.hour * 60 + _MARKET_OPEN.minute)
    )
    return max(0.0, min(1.0, minutes_since_open / _SESSION_MINUTES))


def compute_intraday_bias(
    prev_day: PreviousDayLevels | None,
    candles_1m: list[Candle],
    vwap: VWAPResult | None,
    current_price: float,
    global_cues: GlobalCues | None = None,
    as_of: datetime | None = None,
    nifty_bias_score: float | None = None,
) -> IntradayBias:
    """Compute composite intraday directional bias.

    All inputs may be None / empty — the function degrades gracefully by
    using only the available factors and re-normalises weights.

    as_of: current timestamp (IST-aware) for time-decaying static weights.
    If None, uses base weights (no decay — backwards-compatible).

    nifty_bias_score: pass NIFTY's computed score [-1,+1] for non-NIFTY symbols
    so the broader market direction feeds into stock/other-index bias.
    Do not pass for NIFTY itself (would be circular).
    """
    progress = _session_progress(as_of)

    # Time-decayed weights for static factors
    w_yesterday = 0.10 * (1.0 - _YESTERDAY_DECAY * progress)
    w_gap = 0.15 * (1.0 - _GAP_DECAY * progress)
    # Freed weight redistributed to dynamic factors
    freed = (0.10 - w_yesterday) + (0.15 - w_gap)
    w_vwap_slope = 0.30 + freed * 0.40
    w_price_vs_vwap = 0.10 + freed * 0.15
    w_move_from_pdc = 0.15 + freed * 0.25
    w_candle_momentum = 0.10 + freed * 0.20
    w_global = 0.10
    w_nifty_bias = 0.05  # only applied when nifty_bias_score is provided

    # ADR-based normalizer: previous day range as % of close.
    # Fallback to 0.5% (safe for indices) when prev_day is unavailable.
    if prev_day is not None and prev_day.pdc > 0 and prev_day.day_range > 0:
        adr_pct = prev_day.day_range / prev_day.pdc * 100.0
    else:
        adr_pct = None
    move_normalizer = (adr_pct * 0.5) if adr_pct else 0.5

    weighted_sum = 0.0
    total_weight = 0.0
    components: dict[str, float | str | None] = {}

    # ------------------------------------------------------------------
    # 1. Yesterday's close_position (time-decayed)
    # ------------------------------------------------------------------
    if prev_day is not None and prev_day.day_range > 0:
        close_pos = (prev_day.pdc - prev_day.pdl) / prev_day.day_range
        yest_signal = (close_pos - 0.5) * 2.0
        weighted_sum += yest_signal * w_yesterday
        total_weight += w_yesterday
        components["yesterday_close_position"] = round(close_pos, 3)
        components["yesterday_signal"] = round(yest_signal, 3)
        components["yesterday_bias"] = prev_day.bias.value
    else:
        components["yesterday_close_position"] = None

    # ------------------------------------------------------------------
    # 2. Gap vs previous day close (time-decayed)
    # ------------------------------------------------------------------
    if prev_day is not None and prev_day.pdc > 0 and candles_1m:
        today_open = candles_1m[0].open
        gap_pct = (today_open - prev_day.pdc) / prev_day.pdc * 100.0
        gap_signal = max(-1.0, min(1.0, gap_pct / move_normalizer))
        weighted_sum += gap_signal * w_gap
        total_weight += w_gap
        components["gap_vs_pdc_pct"] = round(gap_pct, 3)
        components["gap_signal"] = round(gap_signal, 3)
    else:
        components["gap_vs_pdc_pct"] = None

    # ------------------------------------------------------------------
    # 3. VWAP slope over last 30 candles (dynamic, weight increases)
    # ------------------------------------------------------------------
    slope_window = 30
    min_candles = 10
    if vwap is not None and len(candles_1m) >= min_candles:
        recent = candles_1m[-min(slope_window, len(candles_1m)):]
        first_close = recent[0].close
        last_close = recent[-1].close
        if first_close > 0:
            slope_pct = (last_close - first_close) / first_close * 100.0
            normaliser = 0.3 * (len(recent) / 10.0)
            slope_signal = max(-1.0, min(1.0, slope_pct / normaliser))
            weighted_sum += slope_signal * w_vwap_slope
            total_weight += w_vwap_slope
            components["vwap_slope_last_10"] = round(slope_pct, 4)
            components["vwap_slope_signal"] = round(slope_signal, 3)
        else:
            components["vwap_slope_last_10"] = None
    else:
        components["vwap_slope_last_10"] = None

    # ------------------------------------------------------------------
    # 4. Price vs VWAP sign (dynamic, weight increases)
    # ------------------------------------------------------------------
    if vwap is not None:
        price_sign = 1.0 if current_price > vwap.vwap else -1.0
        weighted_sum += price_sign * w_price_vs_vwap
        total_weight += w_price_vs_vwap
        components["price_vs_vwap_sign"] = "above" if price_sign > 0 else "below"
    else:
        components["price_vs_vwap_sign"] = None

    # ------------------------------------------------------------------
    # 5. Global overnight cues (0.10, unchanged)
    # ------------------------------------------------------------------
    if global_cues is not None and global_cues.global_score is not None:
        global_signal = max(-1.0, min(1.0, global_cues.global_score))
        weighted_sum += global_signal * w_global
        total_weight += w_global
        components["global_overnight_score"] = round(global_signal, 3)
    else:
        components["global_overnight_score"] = None

    # ------------------------------------------------------------------
    # 6. Candle momentum — net body direction of last 5 candles (dynamic)
    # ------------------------------------------------------------------
    if len(candles_1m) >= 5:
        recent5 = candles_1m[-5:]
        bullish_body = sum(1 for c in recent5 if c.close > c.open)
        bearish_body = sum(1 for c in recent5 if c.close < c.open)
        momentum_score = (bullish_body - bearish_body) / 5.0
        weighted_sum += momentum_score * w_candle_momentum
        total_weight += w_candle_momentum
        components["candle_momentum_last_5"] = round(momentum_score, 2)
    else:
        components["candle_momentum_last_5"] = None

    # ------------------------------------------------------------------
    # 7. Move from PDC — (current_price - PDC) / PDC (dynamic)
    # Captures total move from yesterday's close, including the gap.
    # Complements gap_vs_pdc: gap captures opening gap, this tracks where
    # price IS now. Gap-up that sells off → gap positive, move_from_pdc zero.
    # ------------------------------------------------------------------
    if prev_day is not None and prev_day.pdc > 0:
        move_pct = (current_price - prev_day.pdc) / prev_day.pdc * 100.0
        move_signal = max(-1.0, min(1.0, move_pct / move_normalizer))
        weighted_sum += move_signal * w_move_from_pdc
        total_weight += w_move_from_pdc
        components["move_from_pdc_pct"] = round(move_pct, 3)
        components["move_from_pdc_signal"] = round(move_signal, 3)
    else:
        components["move_from_pdc_pct"] = None

    # ------------------------------------------------------------------
    # 8. NIFTY bias score — benchmark index direction for non-NIFTY symbols
    # Pass NIFTY's computed score so BANKNIFTY/stocks inherit market context.
    # Never pass for NIFTY itself (circular).
    # ------------------------------------------------------------------
    if nifty_bias_score is not None:
        nifty_signal = max(-1.0, min(1.0, nifty_bias_score))
        weighted_sum += nifty_signal * w_nifty_bias
        total_weight += w_nifty_bias
        components["nifty_bias_score"] = round(nifty_bias_score, 3)
    else:
        components["nifty_bias_score"] = None

    # ------------------------------------------------------------------
    # Combine
    # ------------------------------------------------------------------
    if total_weight == 0:
        score = 0.0
    else:
        score = weighted_sum / total_weight

    score = max(-1.0, min(1.0, score))

    if abs(score) >= _STRONG_THRESHOLD:
        strength = "STRONG"
    elif abs(score) >= _MODERATE_THRESHOLD:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    if score > _MODERATE_THRESHOLD:
        bias = DayBias.BULLISH
    elif score < -_MODERATE_THRESHOLD:
        bias = DayBias.BEARISH
    else:
        bias = DayBias.NEUTRAL

    components["score"] = round(score, 4)
    components["strength"] = strength

    return IntradayBias(bias=bias, score=score, strength=strength, components=components)


def is_blocked_by_bias(
    signal_direction: str,
    intraday_bias: IntradayBias,
) -> bool:
    """Return True if the signal direction should be blocked by a STRONG opposing bias.

    STRONG BULLISH bias blocks PE signals.
    STRONG BEARISH bias blocks CE signals.
    MODERATE or WEAK bias allows both; the mismatch is reflected in the
    bias_alignment confidence factor rather than being a hard veto.
    """
    if intraday_bias.strength != "STRONG":
        return False

    is_bullish_signal = signal_direction.upper() in ("CE", "BUY_CE", "CALL")
    is_bullish_bias = intraday_bias.bias == DayBias.BULLISH

    # Block only when bias strongly opposes the signal direction
    return is_bullish_bias and not is_bullish_signal or \
           not is_bullish_bias and is_bullish_signal and intraday_bias.bias == DayBias.BEARISH
