import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class DailySummary(Base, TimestampMixin):
    __tablename__ = "daily_summaries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    trade_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    total_trades: Mapped[int] = mapped_column(nullable=False, default=0)
    winning_trades: Mapped[int] = mapped_column(nullable=False, default=0)
    losing_trades: Mapped[int] = mapped_column(nullable=False, default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    capital_deployed: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    strategies_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    india_vix_open: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    cpr_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
