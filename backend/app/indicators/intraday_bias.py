"""Intraday bias — composite live directional bias for Strategy 2.

Replaces the yesterday-only hard gate in strategy_2_vwap_pullback.py.
Yesterday's bias is now one weighted input rather than a binary gate,
so a bearish gap-down after a bullish yesterday can still trigger PE signals.

Factors and weights
-------------------
yesterday_close_position   0.25  Primary regime signal, but not decisive alone
gap_vs_pdc                 0.20  Pre-open / opening gap direction
vwap_slope                 0.25  Intraday trend revealed by VWAP slope over last 10 candles
price_vs_vwap              0.10  Which side of VWAP price is on right now
global_overnight            0.10  Dow futures, S&P close, USD/INR overnight cues
candle_momentum             0.10  Net direction of last 5 candle bodies

Score in [-1, +1]: positive = bullish bias, negative = bearish bias.
Strength: STRONG |score|>=0.50, MODERATE >=0.20, WEAK otherwise.
"""

from dataclasses import dataclass

from app.core.enums import DayBias
from app.indicators.candle_patterns import Candle
from app.indicators.global_market import GlobalCues
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult


@dataclass
class IntradayBias:
    bias: DayBias
    score: float           # Signed [-1, +1]
    strength: str          # "STRONG" | "MODERATE" | "WEAK"
    components: dict       # Individual factor contributions (for debug/UI)


_STRONG_THRESHOLD = 0.50
_MODERATE_THRESHOLD = 0.20


def compute_intraday_bias(
    prev_day: PreviousDayLevels | None,
    candles_1m: list[Candle],
    vwap: VWAPResult | None,
    current_price: float,
    global_cues: GlobalCues | None = None,
) -> IntradayBias:
    """Compute composite intraday directional bias.

    All inputs may be None / empty — the function degrades gracefully by
    using only the available factors and re-normalises weights.
    """
    weighted_sum = 0.0
    total_weight = 0.0
    components: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 1. Yesterday's close_position (0.25)
    # ------------------------------------------------------------------
    if prev_day is not None and prev_day.day_range > 0:
        close_pos = (prev_day.pdc - prev_day.pdl) / prev_day.day_range  # 0=at low, 1=at high
        # Map to [-1, +1]: 0→-1, 0.5→0, 1→+1
        yest_signal = (close_pos - 0.5) * 2.0
        weighted_sum += yest_signal * 0.25
        total_weight += 0.25
        components["yesterday_close_position"] = round(close_pos, 3)
        components["yesterday_signal"] = round(yest_signal, 3)
        components["yesterday_bias"] = prev_day.bias.value
    else:
        components["yesterday_close_position"] = None

    # ------------------------------------------------------------------
    # 2. Gap vs previous day close (0.20)
    # ------------------------------------------------------------------
    if prev_day is not None and prev_day.pdc > 0 and candles_1m:
        today_open = candles_1m[0].open
        gap_pct = (today_open - prev_day.pdc) / prev_day.pdc * 100.0
        # Normalise: ±0.5% gap = ±1.0 signal (cap at ±2%)
        gap_signal = max(-1.0, min(1.0, gap_pct / 0.5))
        weighted_sum += gap_signal * 0.20
        total_weight += 0.20
        components["gap_vs_pdc_pct"] = round(gap_pct, 3)
        components["gap_signal"] = round(gap_signal, 3)
    else:
        components["gap_vs_pdc_pct"] = None

    # ------------------------------------------------------------------
    # 3. VWAP slope over last 10 candles (0.25)
    # ------------------------------------------------------------------
    if vwap is not None and len(candles_1m) >= 10:
        # Use close prices of the last 10 candles to estimate slope
        recent = candles_1m[-10:]
        first_close = recent[0].close
        last_close = recent[-1].close
        if first_close > 0:
            slope_pct = (last_close - first_close) / first_close * 100.0
            # Normalise: ±0.3% over 10 candles = ±1.0 signal
            slope_signal = max(-1.0, min(1.0, slope_pct / 0.3))
            weighted_sum += slope_signal * 0.25
            total_weight += 0.25
            components["vwap_slope_last_10"] = round(slope_pct, 4)
            components["vwap_slope_signal"] = round(slope_signal, 3)
        else:
            components["vwap_slope_last_10"] = None
    else:
        components["vwap_slope_last_10"] = None

    # ------------------------------------------------------------------
    # 4. Price vs VWAP sign (0.10)
    # ------------------------------------------------------------------
    if vwap is not None:
        price_sign = 1.0 if current_price > vwap.vwap else -1.0
        weighted_sum += price_sign * 0.10
        total_weight += 0.10
        components["price_vs_vwap_sign"] = "above" if price_sign > 0 else "below"
    else:
        components["price_vs_vwap_sign"] = None

    # ------------------------------------------------------------------
    # 5. Global overnight cues (0.10)
    # ------------------------------------------------------------------
    if global_cues is not None and global_cues.global_score is not None:
        global_signal = max(-1.0, min(1.0, global_cues.global_score))
        weighted_sum += global_signal * 0.10
        total_weight += 0.10
        components["global_overnight_score"] = round(global_signal, 3)
    else:
        components["global_overnight_score"] = None

    # ------------------------------------------------------------------
    # 6. Candle momentum — net body direction of last 5 candles (0.10)
    # ------------------------------------------------------------------
    if len(candles_1m) >= 5:
        recent5 = candles_1m[-5:]
        bullish_body = sum(1 for c in recent5 if c.close > c.open)
        bearish_body = sum(1 for c in recent5 if c.close < c.open)
        momentum_score = (bullish_body - bearish_body) / 5.0  # [-1, +1]
        weighted_sum += momentum_score * 0.10
        total_weight += 0.10
        components["candle_momentum_last_5"] = round(momentum_score, 2)
    else:
        components["candle_momentum_last_5"] = None

    # ------------------------------------------------------------------
    # Combine
    # ------------------------------------------------------------------
    if total_weight == 0:
        score = 0.0
    else:
        score = weighted_sum / total_weight  # Normalise to [-1, +1]

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
