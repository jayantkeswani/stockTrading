"""Pure position-sizing helpers.

Extracted from auto_executor.py and signals.py to eliminate duplication.
Callers pass explicit parameter values (not settings) for testability.
"""


def calculate_lots(
    capital: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss: float,
    lot_size: int,
) -> int:
    """Calculate number of lots based on risk per trade.

    Returns at least 1 lot.

    Args:
        capital: Total trading capital in INR.
        risk_per_trade_pct: Percentage of capital to risk per trade (e.g. 2.0 = 2%).
        entry_price: Option/futures entry premium.
        stop_loss: Option/futures stop-loss premium.
        lot_size: Lot size for the instrument.
    """
    risk_amount = capital * (risk_per_trade_pct / 100.0)
    risk_per_lot = abs(entry_price - stop_loss) * lot_size
    if risk_per_lot <= 0:
        return 1
    lots = int(risk_amount / risk_per_lot)
    return max(lots, 1)
