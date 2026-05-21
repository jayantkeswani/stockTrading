"""Margin computation utility using tiered heuristic lookup."""

from app.core.constants import MARGIN_TIER_DEFAULT, MARGIN_TIER_MAP


def compute_margin(
    symbol: str,
    entry_price: float,
    quantity: int,
    instrument_type: str,
) -> float:
    """Compute estimated margin required for a trade.

    Options: margin = premium paid = entry_price * quantity (no leverage).
    Futures: margin = contract_value * tier_percentage (SPAN + exposure).
    """
    if instrument_type == "OPTION":
        return entry_price * quantity

    margin_pct = MARGIN_TIER_MAP.get(symbol, MARGIN_TIER_DEFAULT)
    contract_value = entry_price * quantity
    return contract_value * margin_pct
