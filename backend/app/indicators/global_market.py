"""Global market context indicators — pure functions, no DB/Redis access.

Provides helpers for computing pre-open gap direction, overnight bias from
global markets (US futures, Asian indices, commodities, FX), and a composite
global sentiment score.

The GlobalCues dataclass is the canonical carrier of global market data into
MarketContext. It is populated by strategy_runner from Redis (live path) or
from GlobalMarketSnapshot rows (backtest path).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.enums import DayBias


@dataclass
class GlobalCues:
    """Snapshot of global market indicators at a point in time."""
    # Equity indices (% change from prior close)
    dow_futures_pct: float | None = None      # Dow Jones futures
    sp500_close_pct: float | None = None      # S&P 500 prior-day close change
    nasdaq_close_pct: float | None = None     # Nasdaq prior-day close change
    nifty_pct: float | None = None            # ^NSEI as SGX proxy

    # Commodities & FX
    crude_pct: float | None = None            # WTI crude (CL=F)
    usdinr_pct: float | None = None           # USD/INR (positive = INR weaker)
    dxy_pct: float | None = None              # DXY (positive = USD stronger)

    # Volatility
    us_vix: float | None = None              # VIX level (not % change)

    # Derived from Nifty/SGX and prior close — set by task if available
    pre_open_gap_pct: float | None = None     # (sgx_nifty - prev_close) / prev_close * 100

    # Raw absolute values for snapshots
    dow_futures_price: float | None = None
    sp500_price: float | None = None
    nasdaq_price: float | None = None
    nifty_price: float | None = None
    crude_price: float | None = None
    usdinr_price: float | None = None
    dxy_price: float | None = None

    # Combined score [-1, +1] computed by combined_global_score()
    global_score: float | None = None


def compute_pre_open_gap(sgx_or_nifty_price: float, prev_close: float) -> float:
    """Return gap-up/down percentage vs previous close.

    Positive → gap up (bullish), negative → gap down (bearish).
    """
    if prev_close <= 0:
        return 0.0
    return (sgx_or_nifty_price - prev_close) / prev_close * 100.0


def overnight_bias(
    dow_futures_pct: float | None,
    sp500_change_pct: float | None,
    us_vix: float | None,
) -> DayBias:
    """Derive a rough overnight bias from US market signals.

    Returns BULLISH / BEARISH / NEUTRAL.
    """
    signals: list[float] = []

    if dow_futures_pct is not None:
        signals.append(dow_futures_pct)
    if sp500_change_pct is not None:
        signals.append(sp500_change_pct)

    if not signals:
        return DayBias.NEUTRAL

    avg = sum(signals) / len(signals)

    # Extreme VIX overrides weak signal
    if us_vix is not None and us_vix >= 25:
        return DayBias.BEARISH

    if avg > 0.3:
        return DayBias.BULLISH
    if avg < -0.3:
        return DayBias.BEARISH
    return DayBias.NEUTRAL


def combined_global_score(cues: GlobalCues) -> float:
    """Compute a weighted global sentiment score in [-1, +1].

    Positive → global tailwinds (bullish bias for Nifty).
    Negative → global headwinds (bearish bias).

    Weights:
      dow_futures  0.35 (most real-time US signal)
      sp500_close  0.25 (prior-day context)
      nasdaq_close 0.15 (risk-on/risk-off)
      crude        0.10 (negative for India if high)
      usdinr       0.10 (negative if INR weakens = outflows)
      dxy          0.05 (dollar strength hurts EM)
    """
    score = 0.0
    total_weight = 0.0

    def _add(value: float | None, weight: float, inverted: bool = False) -> None:
        nonlocal score, total_weight
        if value is None:
            return
        direction = -value if inverted else value
        # Normalise: cap at ±2% move → ±1.0 contribution
        normalised = max(-1.0, min(1.0, direction / 2.0))
        score += normalised * weight
        total_weight += weight

    _add(cues.dow_futures_pct, 0.35)
    _add(cues.sp500_close_pct, 0.25)
    _add(cues.nasdaq_close_pct, 0.15)
    _add(cues.crude_pct, 0.10, inverted=True)   # Rising crude hurts India
    _add(cues.usdinr_pct, 0.10, inverted=True)  # Weaker INR hurts inflows
    _add(cues.dxy_pct, 0.05, inverted=True)     # Stronger USD hurts EM

    if total_weight == 0:
        return 0.0
    return score / total_weight


def global_alignment_factor(cues: GlobalCues, direction: str) -> float:
    """Return a 0-1 factor representing how well global cues align with the trade direction.

    direction: 'CE' / 'CALL' / 'BULLISH'  or  'PE' / 'PUT' / 'BEARISH'
    """
    score = cues.global_score
    if score is None:
        score = combined_global_score(cues)

    bullish_direction = direction.upper() in ("CE", "CALL", "BUY_CE", "BULLISH")

    if bullish_direction:
        # +1 global score → factor 1.0; -1 → factor 0.0
        return (score + 1.0) / 2.0
    else:
        # -1 global score → factor 1.0; +1 → factor 0.0
        return (-score + 1.0) / 2.0
