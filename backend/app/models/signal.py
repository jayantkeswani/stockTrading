import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class Signal(Base, TimestampMixin):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY_CE, BUY_PE, BUY_FUT, SELL_FUT
    instrument_type: Mapped[str] = mapped_column(String(10), nullable=False, default="OPTION")  # OPTION, FUTURE, EQUITY
    strike_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    indicators: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    index_entry_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    fyers_option_symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fyers_futures_symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    executed_trade_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sizing_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_signals_status", "status"),
        Index("idx_signals_generated_at", "generated_at"),
    )
