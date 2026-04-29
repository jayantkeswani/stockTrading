"""Deterministic confidence composite for Strategy 2 signals.

Replaces the hand-coded base=70 ± constants in strategy_2_vwap_pullback.py.
Each factor returns a 0-1 float; the weighted sum is scaled to 0-100.

Factor weights (total = 1.0)
-----------------------------
bias_alignment         0.20   How strongly intraday_bias matches signal direction
vwap_slope_alignment   0.10   VWAP slope direction matches signal
reversal_quality       0.15   Candle reversal magnitude (body size, wick ratio)
volume_quality         0.10   Pullback volume is below average (healthy pullback)
rr_ratio_quality       0.10   Index-level R:R ratio (from market structure SL/target)
oi_support             0.10   OI walls confirm the trade direction
cpr_narrow_trending    0.05   Narrow CPR = trending day = better for directional trades
vix_regime             0.05   VIX in 10-18 range = cheap options + stable regime
global_alignment       0.10   Global overnight cues match direction
time_of_day            0.05   In primary trade window = full weight; outside = half

The score from compute_confidence() is the DETERMINISTIC base.
The LLM overlay in signal_confidence.py may adjust it by ±15.
"""

from dataclasses import dataclass

from app.core.constants import POSITION_CLOSE_DEADLINE, WINDOW_1_END, WINDOW_1_START, WINDOW_2_END, WINDOW_2_START
from app.core.enums import DayBias
from app.indicators.candle_patterns import Candle, average_volume
from app.indicators.global_market import GlobalCues, global_alignment_factor
from app.indicators.intraday_bias import IntradayBias
from app.indicators.open_interest import OIAnalysis
from app.indicators.vwap import VWAPResult
from app.indicators.cpr import CPRResult


@dataclass
class ConfidenceResult:
    score: float          # 0-100, pre-LLM deterministic score
    factors: dict         # {factor_name: float 0-1} for UI/debug
    rationale_short: str  # One-line summary of dominant factors


_WEIGHTS = {
    "bias_alignment":       0.20,
    "vwap_slope_alignment": 0.10,
    "reversal_quality":     0.15,
    "volume_quality":       0.10,
    "rr_ratio_quality":     0.10,
    "oi_support":           0.10,
    "cpr_narrow_trending":  0.05,
    "vix_regime":           0.05,
    "global_alignment":     0.10,
    "time_of_day":          0.05,
}


def compute_confidence(
    signal_direction: str,          # "CE" or "PE"
    intraday_bias: IntradayBias | None,
    candles_5m: list[Candle],
    vwap: VWAPResult | None,
    oi_analysis: OIAnalysis | None,
    cpr: CPRResult | None,
    india_vix: float | None,
    global_cues: GlobalCues | None,
    index_sl: float | None,
    index_target: float | None,
    index_entry: float,
    current_time_ist: str = "",
    window_state: str | None = None,
    candles_5m_futures_volume: list[Candle] | None = None,
) -> ConfidenceResult:
    """Compute the deterministic confidence composite.

    Returns ConfidenceResult with score 0-100 and per-factor breakdown.
    """
    is_ce = signal_direction.upper() in ("CE", "BUY_CE", "CALL")
    factors: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 1. bias_alignment — how strongly intraday_bias matches signal direction
    # ------------------------------------------------------------------
    if intraday_bias is not None:
        score = intraday_bias.score  # -1 to +1
        # CE wants positive score, PE wants negative score
        alignment = score if is_ce else -score
        # Map [-1,+1] → [0,1]: 0 = fully opposing, 0.5 = neutral, 1 = fully aligned
        factors["bias_alignment"] = (alignment + 1.0) / 2.0
    else:
        factors["bias_alignment"] = 0.5  # Neutral when unknown

    # ------------------------------------------------------------------
    # 2. vwap_slope_alignment — last 10 candle slope matches direction
    # ------------------------------------------------------------------
    if len(candles_5m) >= 4:
        recent = candles_5m[-4:]  # 4 × 5m = 20 min of slope
        slope = (recent[-1].close - recent[0].close) / recent[0].close if recent[0].close else 0
        # +slope = rising, CE wants rising; -slope = falling, PE wants falling
        slope_alignment = slope if is_ce else -slope
        factors["vwap_slope_alignment"] = max(0.0, min(1.0, 0.5 + slope_alignment / 0.003))
    else:
        factors["vwap_slope_alignment"] = 0.5

    # ------------------------------------------------------------------
    # 3. reversal_quality — last two 5m candles form a meaningful reversal
    # ------------------------------------------------------------------
    if len(candles_5m) >= 2:
        prev_c, curr_c = candles_5m[-2], candles_5m[-1]
        prev_range = prev_c.high - prev_c.low
        curr_range = curr_c.high - curr_c.low

        if is_ce:
            # Bullish engulfing quality: curr body bullish + size vs prev body
            curr_body = curr_c.close - curr_c.open
            prev_body = abs(prev_c.close - prev_c.open)
            engulf_ratio = curr_body / (prev_body + 1e-6) if curr_body > 0 else 0
        else:
            # Bearish engulfing quality
            curr_body = curr_c.open - curr_c.close
            prev_body = abs(prev_c.close - prev_c.open)
            engulf_ratio = curr_body / (prev_body + 1e-6) if curr_body > 0 else 0

        # Also reward small wicks on the entry side (clean reversal)
        wick_clean = 1.0 - (curr_c.high - curr_c.low - abs(curr_c.close - curr_c.open)) / (curr_range + 1e-6)
        wick_clean = max(0.0, wick_clean)
        reversal_raw = min(1.0, engulf_ratio * 0.7 + wick_clean * 0.3)
        factors["reversal_quality"] = reversal_raw
    else:
        factors["reversal_quality"] = 0.5

    # ------------------------------------------------------------------
    # 4. volume_quality — pullback on low volume, breakout on rising volume
    # ------------------------------------------------------------------
    vol_candles = candles_5m_futures_volume or candles_5m
    if len(vol_candles) >= 5:
        avg_vol = average_volume(vol_candles[:-1], periods=min(20, len(vol_candles) - 1))
        curr_vol = vol_candles[-1].volume
        if avg_vol > 0:
            vol_ratio = curr_vol / avg_vol
            # Ideal: pullback vol < 0.8 of avg (quality pullback). Cap reward at 0.5 ratio (very light).
            factors["volume_quality"] = max(0.0, min(1.0, 1.2 - vol_ratio))
        else:
            factors["volume_quality"] = 0.5
    else:
        factors["volume_quality"] = 0.5

    # ------------------------------------------------------------------
    # 5. rr_ratio_quality — index-level R:R
    # ------------------------------------------------------------------
    if index_sl is not None and index_target is not None and index_entry > 0:
        risk = abs(index_entry - index_sl)
        reward = abs(index_target - index_entry)
        rr = reward / (risk + 1e-6)
        # RR 1.0 → 0.5, RR 1.5 → 0.75, RR 2.0 → 1.0, RR ≥ 2.0 → 1.0
        factors["rr_ratio_quality"] = max(0.0, min(1.0, (rr - 1.0) / 1.0 * 0.5 + 0.5))
    else:
        factors["rr_ratio_quality"] = 0.5

    # ------------------------------------------------------------------
    # 6. oi_support — OI walls confirm direction
    # ------------------------------------------------------------------
    if oi_analysis is not None:
        if is_ce:
            # CE: PE wall below price = support floor
            oi_confirmed = index_entry > oi_analysis.max_pe_oi_strike
        else:
            # PE: CE wall above price = resistance ceiling
            oi_confirmed = index_entry < oi_analysis.max_ce_oi_strike
        factors["oi_support"] = 1.0 if oi_confirmed else 0.3
    else:
        factors["oi_support"] = 0.5  # Unknown

    # ------------------------------------------------------------------
    # 7. cpr_narrow_trending — narrow CPR = trending day
    # ------------------------------------------------------------------
    if cpr is not None:
        is_narrow = cpr.cpr_type.value == "NARROW"
        factors["cpr_narrow_trending"] = 1.0 if is_narrow else 0.3
    else:
        factors["cpr_narrow_trending"] = 0.5

    # ------------------------------------------------------------------
    # 8. vix_regime — 10-18 = ideal, extremes penalised
    # ------------------------------------------------------------------
    if india_vix is not None:
        if 10 <= india_vix < 14:
            factors["vix_regime"] = 1.0   # Cheap options, low fear
        elif 14 <= india_vix < 18:
            factors["vix_regime"] = 0.75  # Acceptable
        elif 18 <= india_vix < 22:
            factors["vix_regime"] = 0.4   # Elevated, reduce confidence
        else:
            factors["vix_regime"] = 0.1   # Extreme — signal should be blocked by guardrail anyway
    else:
        factors["vix_regime"] = 0.5

    # ------------------------------------------------------------------
    # 9. global_alignment — overnight cues match direction
    # ------------------------------------------------------------------
    if global_cues is not None:
        factors["global_alignment"] = global_alignment_factor(global_cues, signal_direction)
    else:
        factors["global_alignment"] = 0.5

    # ------------------------------------------------------------------
    # 10. time_of_day — primary windows get full credit
    # ------------------------------------------------------------------
    try:
        if window_state is not None:
            state = window_state
        elif current_time_ist:
            from datetime import datetime as _dt
            from app.core.utils import get_window_state as _get_ws
            ts = _dt.fromisoformat(current_time_ist)
            state = _get_ws(as_of=ts)
        else:
            state = None

        if state == "IN_WINDOW":
            factors["time_of_day"] = 1.0
        elif state == "DEAD_ZONE":
            factors["time_of_day"] = 0.2
        elif state is not None:
            factors["time_of_day"] = 0.5
        else:
            factors["time_of_day"] = 0.5
    except Exception:
        factors["time_of_day"] = 0.5

    # ------------------------------------------------------------------
    # Combine into 0-100 score
    # ------------------------------------------------------------------
    weighted_sum = sum(factors[k] * _WEIGHTS[k] for k in _WEIGHTS)
    # weighted_sum is in [0, 1] → scale to [0, 100]
    score = round(min(100.0, max(0.0, weighted_sum * 100.0)), 1)

    # Build short rationale from top 3 contributing factors
    sorted_factors = sorted(
        [(k, factors[k] * _WEIGHTS[k]) for k in _WEIGHTS],
        key=lambda x: x[1], reverse=True,
    )
    top = [k for k, _ in sorted_factors[:3]]
    bottom = [k for k, v in sorted_factors if v < _WEIGHTS[k] * 0.4]

    rationale_parts = []
    if top:
        rationale_parts.append(f"Strong: {', '.join(top)}")
    if bottom:
        rationale_parts.append(f"Weak: {', '.join(bottom[:2])}")
    rationale_short = " | ".join(rationale_parts) or "Balanced setup"

    return ConfidenceResult(score=score, factors=factors, rationale_short=rationale_short)
