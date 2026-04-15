"""Central Pivot Range (CPR) calculation.

CPR helps identify day type:
- Narrow CPR = trending day expected (good for breakout strategies)
- Wide CPR = range-bound day expected (good for mean-reversion)
"""

from dataclasses import dataclass
from app.core.enums import CPRType


@dataclass
class CPRResult:
    pivot: float
    tc: float  # Top Central
    bc: float  # Bottom Central
    r1: float  # Resistance 1
    s1: float  # Support 1
    r2: float  # Resistance 2
    s2: float  # Support 2
    cpr_type: CPRType
    cpr_width_pct: float  # Width as % of pivot


def calculate_cpr(
    high: float,
    low: float,
    close: float,
    narrow_threshold_pct: float = 0.1,
) -> CPRResult:
    """Calculate CPR from previous day's High, Low, Close.

    Args:
        high: Previous day high
        low: Previous day low
        close: Previous day close
        narrow_threshold_pct: Threshold to classify as narrow CPR

    Returns:
        CPRResult with all pivot levels and day type classification
    """
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = (pivot - bc) + pivot

    # Ensure tc > bc
    if tc < bc:
        tc, bc = bc, tc

    # Standard pivot levels
    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)

    # CPR width classification
    cpr_width = tc - bc
    cpr_width_pct = (cpr_width / pivot) * 100 if pivot > 0 else 0

    cpr_type = CPRType.NARROW if cpr_width_pct < narrow_threshold_pct else CPRType.WIDE

    return CPRResult(
        pivot=pivot,
        tc=tc,
        bc=bc,
        r1=r1,
        s1=s1,
        r2=r2,
        s2=s2,
        cpr_type=cpr_type,
        cpr_width_pct=cpr_width_pct,
    )
