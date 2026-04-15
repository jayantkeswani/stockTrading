"""Previous day analysis for directional bias.

Analyzes previous day's OHLC to determine:
- Day bias (BULLISH/BEARISH/NEUTRAL)
- Key levels (PDH, PDL, PDC)
- Daily range characteristics
"""

from dataclasses import dataclass
from app.core.enums import DayBias


@dataclass
class PreviousDayLevels:
    pdh: float  # Previous Day High
    pdl: float  # Previous Day Low
    pdc: float  # Previous Day Close
    pdo: float  # Previous Day Open
    day_range: float  # PDH - PDL
    bias: DayBias


def analyze_previous_day(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> PreviousDayLevels:
    """Analyze previous day candle for directional bias.

    Bullish: close > open AND close in upper 30% of range
    Bearish: close < open AND close in lower 30% of range
    Neutral: neither condition
    """
    day_range = high - low
    if day_range == 0:
        return PreviousDayLevels(
            pdh=high, pdl=low, pdc=close, pdo=open_price,
            day_range=0, bias=DayBias.NEUTRAL,
        )

    # Where did price close relative to the range?
    close_position = (close - low) / day_range  # 0 = at low, 1 = at high

    if close > open_price and close_position >= 0.7:
        bias = DayBias.BULLISH
    elif close < open_price and close_position <= 0.3:
        bias = DayBias.BEARISH
    else:
        bias = DayBias.NEUTRAL

    return PreviousDayLevels(
        pdh=high, pdl=low, pdc=close, pdo=open_price,
        day_range=day_range, bias=bias,
    )


def is_gap_up(current_open: float, pdc: float, threshold_pct: float = 0.3) -> bool:
    """Check if today opened with a gap up (> threshold % above PDC)."""
    if pdc == 0:
        return False
    gap_pct = ((current_open - pdc) / pdc) * 100
    return gap_pct > threshold_pct


def is_gap_down(current_open: float, pdc: float, threshold_pct: float = 0.3) -> bool:
    """Check if today opened with a gap down (> threshold % below PDC)."""
    if pdc == 0:
        return False
    gap_pct = ((pdc - current_open) / pdc) * 100
    return gap_pct > threshold_pct
