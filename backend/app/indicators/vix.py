"""India VIX processor for position sizing."""

from app.core.constants import VIX_EXTREME, VIX_HIGH, VIX_LOW


def get_position_size_multiplier(vix: float) -> float:
    """Get position size multiplier based on India VIX.

    VIX < 14: Full position (1.0)
    VIX 14-18: Full position (1.0)
    VIX 18-22: Reduced by 30% (0.7)
    VIX > 22: Do not trade (0.0)
    """
    if vix >= VIX_EXTREME:
        return 0.0
    if vix >= VIX_HIGH:
        return 0.7
    return 1.0


def classify_vix(vix: float) -> str:
    """Classify VIX level for display."""
    if vix < VIX_LOW:
        return "LOW"
    if vix < VIX_HIGH:
        return "NORMAL"
    if vix < VIX_EXTREME:
        return "HIGH"
    return "EXTREME"
