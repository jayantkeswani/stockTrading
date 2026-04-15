"""Open Interest analysis for institutional confirmation.

Tracks OI distribution across strikes to identify:
- Max Pain level
- Support/Resistance from OI
- PCR (Put-Call Ratio) for sentiment
"""

from dataclasses import dataclass


@dataclass
class OIAnalysis:
    pcr: float  # Put-Call Ratio
    max_ce_oi_strike: float  # Strike with highest Call OI (resistance)
    max_pe_oi_strike: float  # Strike with highest Put OI (support)
    total_ce_oi: int
    total_pe_oi: int
    max_pain: float  # Max pain strike
    sentiment: str  # "BULLISH", "BEARISH", "NEUTRAL"


def analyze_option_chain(
    strikes: list[dict],
) -> OIAnalysis | None:
    """Analyze option chain OI data.

    Args:
        strikes: List of dicts with keys:
            strike_price, ce_oi, pe_oi, ce_volume, pe_volume

    Returns:
        OIAnalysis or None if insufficient data
    """
    if not strikes:
        return None

    total_ce_oi = sum(s.get("ce_oi", 0) for s in strikes)
    total_pe_oi = sum(s.get("pe_oi", 0) for s in strikes)

    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0

    # Max CE OI strike = resistance
    max_ce_strike = max(strikes, key=lambda s: s.get("ce_oi", 0))
    max_ce_oi_strike = max_ce_strike["strike_price"]

    # Max PE OI strike = support
    max_pe_strike = max(strikes, key=lambda s: s.get("pe_oi", 0))
    max_pe_oi_strike = max_pe_strike["strike_price"]

    # Max Pain calculation (strike where total option premium loss is minimum)
    max_pain = _calculate_max_pain(strikes)

    # Sentiment
    if pcr < 0.7:
        sentiment = "BULLISH"  # More calls than puts, contrarian bullish
    elif pcr > 1.5:
        sentiment = "BEARISH"  # More puts than calls, contrarian bearish
    else:
        sentiment = "NEUTRAL"

    return OIAnalysis(
        pcr=pcr,
        max_ce_oi_strike=max_ce_oi_strike,
        max_pe_oi_strike=max_pe_oi_strike,
        total_ce_oi=total_ce_oi,
        total_pe_oi=total_pe_oi,
        max_pain=max_pain,
        sentiment=sentiment,
    )


def _calculate_max_pain(strikes: list[dict]) -> float:
    """Calculate max pain strike (where total buyer losses are maximum)."""
    min_loss = float("inf")
    max_pain_strike = 0

    for target in strikes:
        target_price = target["strike_price"]
        total_loss = 0

        for s in strikes:
            sp = s["strike_price"]
            ce_oi = s.get("ce_oi", 0)
            pe_oi = s.get("pe_oi", 0)

            # CE loss: max(0, target_price - strike_price) * ce_oi
            ce_loss = max(0, target_price - sp) * ce_oi
            # PE loss: max(0, strike_price - target_price) * pe_oi
            pe_loss = max(0, sp - target_price) * pe_oi

            total_loss += ce_loss + pe_loss

        if total_loss < min_loss:
            min_loss = total_loss
            max_pain_strike = target_price

    return max_pain_strike


def is_oi_supporting_direction(
    current_price: float,
    max_pe_oi_strike: float,
    max_ce_oi_strike: float,
    direction: str,  # "CALL" or "PUT"
) -> bool:
    """Check if OI supports the trade direction.

    For CALL: current price should be above max PE OI strike (support holds)
    For PUT: current price should be below max CE OI strike (resistance holds)
    """
    if direction == "CALL":
        return current_price > max_pe_oi_strike
    elif direction == "PUT":
        return current_price < max_ce_oi_strike
    return False
