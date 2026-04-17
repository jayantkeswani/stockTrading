"""Market structure level selection for index-level SL/target.

Selects meaningful stop-loss and target prices from market structure:
- VWAP bands (upper/lower)
- Previous day levels (PDH/PDL)
- CPR pivot levels (BC, TC, S1, R1, S2, R2)
- OI walls (max CE/PE OI strikes)
- Recent swing highs/lows from intraday candles

Used by option-based strategies to emit index-level SL/target that the
option_resolver then converts to premium-equivalent prices via delta.
"""

from app.core.enums import SignalType
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import CPRResult
from app.indicators.open_interest import OIAnalysis
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult


def find_swing_low(candles: list[Candle], lookback: int = 10) -> float | None:
    """Return the lowest low of the last *lookback* candles."""
    if not candles:
        return None
    window = candles[-lookback:]
    return min(c.low for c in window)


def find_swing_high(candles: list[Candle], lookback: int = 10) -> float | None:
    """Return the highest high of the last *lookback* candles."""
    if not candles:
        return None
    window = candles[-lookback:]
    return max(c.high for c in window)


def select_index_sl_target(
    entry_price: float,
    signal_type: SignalType,
    vwap: VWAPResult | None = None,
    previous_day: PreviousDayLevels | None = None,
    cpr: CPRResult | None = None,
    oi_analysis: OIAnalysis | None = None,
    candles_5m: list[Candle] | None = None,
    min_buffer_pct: float = 0.10,
) -> tuple[float | None, float | None]:
    """Select index-level SL and target from market structure.

    For BUY_CE: SL is below entry (support), target is above entry (resistance).
    For BUY_PE: SL is above entry (resistance), target is below entry (support).

    Args:
        entry_price: Current index price at signal time.
        signal_type: BUY_CE or BUY_PE.
        vwap: VWAP with upper/lower bands.
        previous_day: PDH, PDL, PDC levels.
        cpr: Central Pivot Range levels.
        oi_analysis: OI-based support/resistance strikes.
        candles_5m: Recent 5-minute candles for swing detection.
        min_buffer_pct: Minimum distance from entry (% of price) for a level
            to qualify. Prevents SL/target that are too close.

    Returns:
        (index_sl, index_target) — either or both may be None if no valid
        levels are found.
    """
    min_buffer = entry_price * (min_buffer_pct / 100)

    if signal_type == SignalType.BUY_CE:
        index_sl = _select_support(entry_price, min_buffer, vwap, previous_day, cpr, oi_analysis, candles_5m)
        index_target = _select_resistance(entry_price, min_buffer, vwap, previous_day, cpr, oi_analysis, candles_5m)
    elif signal_type == SignalType.BUY_PE:
        index_sl = _select_resistance(entry_price, min_buffer, vwap, previous_day, cpr, oi_analysis, candles_5m)
        index_target = _select_support(entry_price, min_buffer, vwap, previous_day, cpr, oi_analysis, candles_5m)
    else:
        return None, None

    # R:R check — skip if reward < risk
    if index_sl is not None and index_target is not None:
        rr = compute_rr_ratio(entry_price, index_sl, index_target, signal_type)
        if rr < 1.0:
            return None, None

    return index_sl, index_target


def compute_rr_ratio(
    entry_price: float,
    index_sl: float,
    index_target: float,
    signal_type: SignalType,
) -> float:
    """Compute reward-to-risk ratio from index-level prices.

    Returns 0.0 if risk is zero (to avoid division by zero).
    """
    if signal_type == SignalType.BUY_CE:
        risk = entry_price - index_sl
        reward = index_target - entry_price
    elif signal_type == SignalType.BUY_PE:
        risk = index_sl - entry_price
        reward = entry_price - index_target
    else:
        return 0.0

    if risk <= 0:
        return 0.0
    return reward / risk


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _select_support(
    entry_price: float,
    min_buffer: float,
    vwap: VWAPResult | None,
    previous_day: PreviousDayLevels | None,
    cpr: CPRResult | None,
    oi_analysis: OIAnalysis | None,
    candles_5m: list[Candle] | None,
) -> float | None:
    """Pick the nearest valid support level below entry_price.

    Returns the highest (nearest to entry) candidate that is at least
    min_buffer below entry. Tighter SL = better R:R.
    """
    candidates: list[float] = []

    if vwap:
        candidates.append(vwap.lower_band)

    if candles_5m:
        swing = find_swing_low(candles_5m, lookback=10)
        if swing is not None:
            candidates.append(swing)

    if cpr:
        candidates.extend([cpr.bc, cpr.s1])

    if previous_day:
        candidates.append(previous_day.pdl)

    if oi_analysis and oi_analysis.max_pe_oi_strike:
        candidates.append(oi_analysis.max_pe_oi_strike)

    # Filter: must be below entry by at least min_buffer
    valid = [c for c in candidates if c < entry_price - min_buffer]

    if not valid:
        return None

    # Pick nearest (highest) — tightest SL
    return max(valid)


def _select_resistance(
    entry_price: float,
    min_buffer: float,
    vwap: VWAPResult | None,
    previous_day: PreviousDayLevels | None,
    cpr: CPRResult | None,
    oi_analysis: OIAnalysis | None,
    candles_5m: list[Candle] | None,
) -> float | None:
    """Pick the nearest valid resistance level above entry_price.

    Returns the lowest (nearest to entry) candidate that is at least
    min_buffer above entry. Most achievable target.
    """
    candidates: list[float] = []

    if vwap:
        candidates.append(vwap.upper_band)

    if cpr:
        candidates.extend([cpr.tc, cpr.r1])

    if previous_day:
        candidates.append(previous_day.pdh)

    if oi_analysis and oi_analysis.max_ce_oi_strike:
        candidates.append(oi_analysis.max_ce_oi_strike)

    # Filter: must be above entry by at least min_buffer
    valid = [c for c in candidates if c > entry_price + min_buffer]

    if not valid:
        return None

    # Pick nearest (lowest) — most achievable target
    return min(valid)
