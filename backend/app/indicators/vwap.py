"""VWAP (Volume Weighted Average Price) calculation.

VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
where Typical Price = (High + Low + Close) / 3

Resets daily at market open (9:15 AM IST).
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np


@dataclass
class VWAPResult:
    vwap: float
    upper_band: float  # +1 std dev
    lower_band: float  # -1 std dev


def calculate_vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[int],
) -> VWAPResult | None:
    """Calculate VWAP from intraday candle data.

    All arrays must be same length and represent candles from market open.
    Returns None if insufficient data.
    """
    if len(highs) < 2 or sum(volumes) == 0:
        return None

    h = np.array(highs, dtype=np.float64)
    l = np.array(lows, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)
    v = np.array(volumes, dtype=np.float64)

    typical_price = (h + l + c) / 3.0
    cum_tp_vol = np.cumsum(typical_price * v)
    cum_vol = np.cumsum(v)

    # Avoid division by zero — np.divide(where=) skips masked positions entirely
    mask = cum_vol > 0
    vwap_values = np.divide(cum_tp_vol, cum_vol, out=np.zeros_like(cum_tp_vol), where=mask)
    current_vwap = float(vwap_values[-1])

    # Standard deviation bands
    squared_diff = (typical_price - vwap_values) ** 2
    cum_sq_diff_vol = np.cumsum(squared_diff * v)
    variance = np.divide(cum_sq_diff_vol, cum_vol, out=np.zeros_like(cum_sq_diff_vol), where=mask)
    std_dev = float(np.sqrt(variance[-1]))

    return VWAPResult(
        vwap=current_vwap,
        upper_band=current_vwap + std_dev,
        lower_band=current_vwap - std_dev,
    )


def price_distance_from_vwap(price: float, vwap: float) -> float:
    """Calculate percentage distance of price from VWAP.

    Positive = price above VWAP, Negative = price below VWAP.
    """
    if vwap == 0:
        return 0.0
    return ((price - vwap) / vwap) * 100.0


def is_pullback_to_vwap(
    price: float,
    vwap: float,
    proximity_pct: float = 0.15,
) -> bool:
    """Check if price is pulling back to VWAP (within proximity threshold)."""
    distance = abs(price_distance_from_vwap(price, vwap))
    return distance <= proximity_pct
