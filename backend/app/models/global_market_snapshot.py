import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, generate_uuid


class GlobalMarketSnapshot(Base):
    """15-minute snapshot of global market indicators.

    Populated by global_market_task. Used as the source of truth for:
    - MarketContext.global_cues in live trading (via Redis hot path)
    - Backtest context_builder (historical replay filtered by timestamp <= as_of)
    """
    __tablename__ = "global_market_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Equity indices (% change from prior close, nullable)
    dow_futures_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    sp500_close_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    nasdaq_close_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    nifty_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Commodities & FX (% change, nullable)
    crude_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    usdinr_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    dxy_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Volatility (absolute level, not % change)
    us_vix: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Pre-open gap vs Nifty previous close (set when available)
    pre_open_gap_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Raw absolute prices for reference
    dow_futures_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    sp500_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    nasdaq_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    nifty_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    crude_price: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    usdinr_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    dxy_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Derived composite score [-1, +1]
    global_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    __table_args__ = (
        UniqueConstraint("timestamp", name="uq_global_market_snapshot_ts"),
        Index("idx_global_market_snapshot_ts", "timestamp"),
    )
