"""CAN SLIM fundamental data models.

StockFundamental: Pre-fetched CAN SLIM scores and raw fundamentals per stock.
    Updated periodically by fundamental_data_task (like OI snapshots).
    Read by strategy_runner when building MarketContext for CAN SLIM evaluation.

FundamentalHistory: Quarterly snapshots for trend analysis (EPS acceleration,
    institutional holding changes). One row per symbol per quarter.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class StockFundamental(Base, TimestampMixin):
    """Pre-fetched CAN SLIM fundamental data per stock.

    One row per F&O eligible stock. Updated by the fundamental_data_task
    on startup and every 6 hours. The strategy_runner reads this table
    to populate MarketContext.canslim_data for CAN SLIM evaluation.
    """

    __tablename__ = "stock_fundamentals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    symbol: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    yfinance_ticker: Mapped[str] = mapped_column(String(30), nullable=False)  # e.g. "TCS.NS"

    # Market cap
    market_cap_cr: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # C — Current quarterly earnings
    latest_qtr_eps_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    latest_qtr_revenue_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    eps_accelerating: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # A — Annual earnings growth
    annual_eps_growth_3yr_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    roe_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    operating_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    # S — Supply / Demand
    free_float_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    # L — Leader (Relative Strength)
    relative_strength_rating: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # I — Institutional sponsorship
    fii_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    fii_change_qoq: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    mf_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    mf_change_qoq: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    promoter_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Composite CAN SLIM scores (0-100 each)
    c_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    a_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    n_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    s_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    l_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    i_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    canslim_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Price reference
    price_52w_high: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    pct_from_52w_high: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # F&O metadata
    is_fo_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Sector classification (auto-populated from yfinance)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Refresh tracking
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("idx_stock_fundamentals_symbol", "symbol"),)


class FundamentalHistory(Base, TimestampMixin):
    """Quarterly fundamental snapshots for trend analysis.

    Used to detect EPS acceleration (QoQ growth increasing) and
    institutional holding trends (FII/MF rising quarter-over-quarter).
    """

    __tablename__ = "fundamental_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    quarter_end: Mapped[date] = mapped_column(Date, nullable=False)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    revenue_cr: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    roe_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    fii_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    mf_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    promoter_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "quarter_end", name="uq_fundamental_history_symbol_quarter"),
        Index("idx_fundamental_history_symbol", "symbol"),
    )
