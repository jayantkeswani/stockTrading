import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class Trade(Base, TimestampMixin):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    strike_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)  # CE, PE
    side: Mapped[str] = mapped_column(String(4), nullable=False, default="BUY")
    quantity: Mapped[int] = mapped_column(nullable=False)
    lots: Mapped[int] = mapped_column(nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    pnl_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fyers_option_symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_trades_status", "status"),
        Index("idx_trades_entry_time", "entry_time"),
        Index("idx_trades_strategy", "strategy_name"),
    )
