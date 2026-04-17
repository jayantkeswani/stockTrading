"""CAN SLIM scoring functions — pure, no side effects.

Each function scores a CAN SLIM factor on a 0-100 scale using linear interpolation
between defined thresholds. The composite score is a weighted average of all factors.

Thresholds are India-adapted from William O'Neil's original criteria:
- EPS growth threshold lowered to 20% (from 25%) for Indian market
- ROE threshold at 15% (vs O'Neil's 17%)
- D/E stricter at 1.0 (Indian investor preference for low leverage)
- Free float consideration (Indian promoter-dominated market)
"""

from app.core.constants import CANSLIM_SCORE_WEIGHTS


def _interpolate(value: float, zero_at: float, full_at: float) -> float:
    """Linear interpolation between 0 and 100.

    Returns 0 if value <= zero_at, 100 if value >= full_at,
    linearly interpolated in between.
    """
    if full_at == zero_at:
        return 100.0 if value >= full_at else 0.0
    if full_at > zero_at:
        # Higher is better
        if value <= zero_at:
            return 0.0
        if value >= full_at:
            return 100.0
        return ((value - zero_at) / (full_at - zero_at)) * 100.0
    else:
        # Lower is better (zero_at > full_at)
        if value >= zero_at:
            return 0.0
        if value <= full_at:
            return 100.0
        return ((zero_at - value) / (zero_at - full_at)) * 100.0


def score_c(
    qtr_eps_growth_pct: float | None,
    qtr_rev_growth_pct: float | None,
    eps_accelerating: bool | None,
) -> float:
    """Score Current quarterly earnings (0-100).

    Full marks (100): QoQ EPS growth >= 25%, revenue growth >= 25%, accelerating
    Zero (0): Negative EPS growth or no data
    """
    if qtr_eps_growth_pct is None:
        return 0.0

    # EPS growth is the primary driver (70% of C score)
    eps_score = _interpolate(qtr_eps_growth_pct, zero_at=-5.0, full_at=25.0)

    # Revenue growth is secondary (20% of C score)
    rev_score = 50.0  # Default neutral if missing
    if qtr_rev_growth_pct is not None:
        rev_score = _interpolate(qtr_rev_growth_pct, zero_at=-5.0, full_at=25.0)

    # Acceleration bonus (10% of C score)
    accel_score = 50.0  # Default neutral
    if eps_accelerating is True:
        accel_score = 100.0
    elif eps_accelerating is False:
        accel_score = 20.0

    return eps_score * 0.70 + rev_score * 0.20 + accel_score * 0.10


def score_a(
    annual_eps_growth_3yr_pct: float | None,
    roe_pct: float | None,
    operating_margin_pct: float | None,
) -> float:
    """Score Annual earnings growth (0-100).

    Full marks: 3yr EPS CAGR >= 25%, ROE >= 20%, OPM >= 15%
    Zero: < 5% growth or ROE < 5%
    """
    # Annual growth (50% of A score)
    growth_score = 0.0
    if annual_eps_growth_3yr_pct is not None:
        growth_score = _interpolate(annual_eps_growth_3yr_pct, zero_at=5.0, full_at=25.0)

    # ROE (30% of A score)
    roe_score = 0.0
    if roe_pct is not None:
        roe_score = _interpolate(roe_pct, zero_at=5.0, full_at=20.0)

    # Operating margin (20% of A score)
    opm_score = 50.0  # Default neutral if missing
    if operating_margin_pct is not None:
        opm_score = _interpolate(operating_margin_pct, zero_at=5.0, full_at=15.0)

    return growth_score * 0.50 + roe_score * 0.30 + opm_score * 0.20


def score_n(pct_from_52w_high: float | None) -> float:
    """Score New highs / proximity to 52-week high (0-100).

    Full marks: Within 5% of 52w high
    Zero: More than 25% from 52w high
    """
    if pct_from_52w_high is None:
        return 0.0

    # pct_from_52w_high: 0% = at high, 25% = 25% below high
    # Lower distance is better
    return _interpolate(pct_from_52w_high, zero_at=25.0, full_at=5.0)


def score_s(
    free_float_pct: float | None,
    volume_ratio: float | None,
    debt_to_equity: float | None,
) -> float:
    """Score Supply/Demand (0-100).

    Full marks: Low free float (< 40%), volume surge (> 1.5x), low D/E (< 0.5)
    Zero: D/E > 2 or volume declining
    """
    # Free float: lower is better for supply scarcity (40% of S score)
    float_score = 50.0  # Neutral default
    if free_float_pct is not None:
        float_score = _interpolate(free_float_pct, zero_at=80.0, full_at=40.0)

    # Volume ratio: higher is better (30% of S score)
    vol_score = 50.0
    if volume_ratio is not None:
        vol_score = _interpolate(volume_ratio, zero_at=0.5, full_at=1.5)

    # Debt/equity: lower is better (30% of S score)
    de_score = 50.0
    if debt_to_equity is not None:
        de_score = _interpolate(debt_to_equity, zero_at=2.0, full_at=0.5)

    return float_score * 0.40 + vol_score * 0.30 + de_score * 0.30


def score_l(relative_strength_rating: float | None) -> float:
    """Score Leader / Relative Strength (0-100).

    Full marks: RS >= 90
    Zero: RS < 50
    """
    if relative_strength_rating is None:
        return 0.0
    return _interpolate(relative_strength_rating, zero_at=50.0, full_at=90.0)


def score_i(
    fii_change_qoq: float | None,
    mf_change_qoq: float | None,
) -> float:
    """Score Institutional sponsorship (0-100).

    Full marks: Both FII and MF holdings rising QoQ (> +1%)
    Zero: Both declining (< -1%)
    """
    # FII change (50% of I score)
    fii_score = 50.0  # Neutral if no data
    if fii_change_qoq is not None:
        fii_score = _interpolate(fii_change_qoq, zero_at=-1.0, full_at=1.0)

    # MF change (50% of I score)
    mf_score = 50.0
    if mf_change_qoq is not None:
        mf_score = _interpolate(mf_change_qoq, zero_at=-1.0, full_at=1.0)

    return fii_score * 0.50 + mf_score * 0.50


def score_m(nifty_above_50dma: bool | None, india_vix: float | None) -> float:
    """Score Market direction (0-100).

    Full marks: NIFTY above 50 DMA + VIX < 15
    Zero: NIFTY below 50 DMA + VIX > 25
    """
    # NIFTY trend (60% of M score)
    trend_score = 50.0
    if nifty_above_50dma is True:
        trend_score = 100.0
    elif nifty_above_50dma is False:
        trend_score = 0.0

    # VIX (40% of M score): lower is better
    vix_score = 50.0
    if india_vix is not None:
        vix_score = _interpolate(india_vix, zero_at=25.0, full_at=15.0)

    return trend_score * 0.60 + vix_score * 0.40


def compute_canslim_total(
    c: float,
    a: float,
    n: float,
    s: float,
    l: float,
    i: float,
    m: float,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute weighted CAN SLIM composite score (0-100).

    Default weights: C=20%, A=20%, N=10%, S=10%, L=15%, I=10%, M=15%
    """
    w = weights or CANSLIM_SCORE_WEIGHTS
    total = (
        c * w["C"]
        + a * w["A"]
        + n * w["N"]
        + s * w["S"]
        + l * w["L"]
        + i * w["I"]
        + m * w["M"]
    )
    return min(100.0, max(0.0, total))


# Re-export from canonical location
from app.indicators.relative_strength import compute_rs_raw_score, percentile_rank_rs  # noqa: F401, E402
