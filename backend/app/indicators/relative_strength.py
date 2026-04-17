"""Relative Strength (RS) indicator for CAN SLIM.

Computes IBD-style Relative Strength Rating by comparing a stock's
price performance to a benchmark (NIFTY 50) over the past 12 months.

RS Rating of 80 means the stock outperformed 80% of all other stocks.
"""


def compute_rs_raw_score(daily_closes: list[float]) -> float:
    """Compute raw RS weighted return from daily close prices.

    Uses IBD-style weighted return:
    RS = 0.4 * last_quarter_return + 0.2 * Q2 + 0.2 * Q3 + 0.2 * Q4

    Returns the raw weighted return (can be negative or positive).
    For RS Rating, percentile-rank these raw scores across the stock universe
    using `percentile_rank_rs()`.
    """
    if len(daily_closes) < 60:
        return 0.0  # Insufficient data

    n = len(daily_closes)
    current = daily_closes[-1]
    if current <= 0:
        return 0.0

    q1_start = max(0, n - 63)
    q2_start = max(0, n - 126)
    q3_start = max(0, n - 189)
    q4_start = max(0, n - 252)

    def _return(start_idx: int, end_idx: int) -> float:
        s = daily_closes[start_idx]
        e = daily_closes[end_idx]
        return ((e - s) / s * 100) if s > 0 else 0.0

    q1 = _return(q1_start, n - 1)
    q2 = _return(q2_start, q1_start) if q2_start < q1_start else 0
    q3 = _return(q3_start, q2_start) if q3_start < q2_start else 0
    q4 = _return(q4_start, q3_start) if q4_start < q3_start else 0

    return q1 * 0.4 + q2 * 0.2 + q3 * 0.2 + q4 * 0.2


def percentile_rank_rs(raw_scores: dict[str, float]) -> dict[str, float]:
    """Convert raw RS scores to percentile ranks (1-99).

    IBD RS Rating ranks a stock's price performance against all other stocks.
    A rating of 80 means the stock outperformed 80% of all stocks.

    Args:
        raw_scores: dict of symbol -> raw weighted return from compute_rs_raw_score()

    Returns:
        dict of symbol -> percentile rank (1-99 scale)
    """
    if not raw_scores:
        return {}

    sorted_symbols = sorted(raw_scores, key=lambda s: raw_scores[s])
    n = len(sorted_symbols)

    if n == 1:
        return {sorted_symbols[0]: 50.0}

    ranks: dict[str, float] = {}
    for i, sym in enumerate(sorted_symbols):
        # Percentile: what fraction of stocks did this one beat?
        pct = (i / (n - 1)) * 98 + 1  # Scale to 1-99
        ranks[sym] = round(pct, 1)

    return ranks


def compute_50_dma(daily_closes: list[float]) -> float | None:
    """Compute 50-day simple moving average.

    Returns None if fewer than 50 data points available.
    """
    if len(daily_closes) < 50:
        return None
    return sum(daily_closes[-50:]) / 50


def is_above_50_dma(current_price: float, daily_closes: list[float]) -> bool | None:
    """Check if current price is above the 50-day moving average.

    Returns None if insufficient data.
    """
    dma = compute_50_dma(daily_closes)
    if dma is None:
        return None
    return current_price > dma
