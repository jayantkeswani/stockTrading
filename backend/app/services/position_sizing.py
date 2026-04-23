"""Pure position-sizing helpers."""


def calculate_lots(
    capital: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss: float,
    lot_size: int,
    *,
    vix_multiplier: float = 1.0,
    max_lots: int | None = None,
) -> int:
    """Calculate number of lots based on risk per trade.

    Returns at least 1 lot.

    Args:
        capital: Total trading capital in INR.
        risk_per_trade_pct: Percentage of capital to risk per trade (e.g. 2.0 = 2%).
        entry_price: Option/futures entry premium.
        stop_loss: Option/futures stop-loss premium.
        lot_size: Lot size for the instrument.
        vix_multiplier: Scale lots up/down based on volatility (default 1.0 = no adjustment).
        max_lots: Hard cap on number of lots (None = no cap).
    """
    risk_amount = capital * (risk_per_trade_pct / 100.0)
    risk_per_lot = abs(entry_price - stop_loss) * lot_size
    if risk_per_lot <= 0:
        return 1
    lots = int(risk_amount / risk_per_lot)
    lots = max(1, int(lots * vix_multiplier))
    if max_lots is not None:
        lots = min(lots, max_lots)
    return lots


def vix_to_multiplier(india_vix: float | None) -> float:
    """Convert India VIX level to a lot-sizing multiplier.

    Lower VIX = cheaper options = slightly more exposure.
    Higher VIX = expensive/risky options = reduced exposure.
    """
    if india_vix is None:
        return 1.0
    if india_vix < 14:
        return 1.1
    if india_vix < 18:
        return 1.0
    if india_vix < 22:
        return 0.9
    return 0.8
