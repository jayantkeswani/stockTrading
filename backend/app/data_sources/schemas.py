"""Data transfer objects for fundamental data from external sources."""

from dataclasses import dataclass
from datetime import date


@dataclass
class QuarterlyEarnings:
    """Single quarter's earnings data."""

    quarter_end: date
    eps: float
    revenue_cr: float  # Revenue in crores
    yoy_eps_growth_pct: float | None = None
    yoy_revenue_growth_pct: float | None = None


@dataclass
class AnnualFinancials:
    """Annual financial summary."""

    fiscal_year: int
    eps: float
    revenue_cr: float
    roe_pct: float | None = None
    operating_margin_pct: float | None = None
    debt_to_equity: float | None = None


@dataclass
class ShareholdingPattern:
    """Quarterly shareholding breakdown."""

    quarter_end: date
    promoter_pct: float
    fii_pct: float
    dii_pct: float
    mf_pct: float  # Mutual fund subset of DII
    public_pct: float
    pledge_pct: float | None = None


@dataclass
class PriceHistory:
    """Daily price bar."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class StockInfo:
    """Basic stock metadata."""

    symbol: str
    market_cap_cr: float | None = None
    free_float_pct: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    current_price: float | None = None
