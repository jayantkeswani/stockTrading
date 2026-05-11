import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, generate_uuid


class SignalHistory(Base):
    """Archived snapshot of a Signal before a Case-2 dedup update.

    One row per version. Version 1 = the original signal at first fire.
    Version N = what it looked like just before the Nth update.
    Ordered by version DESC gives the most recent snapshot first.
    """

    __tablename__ = "signal_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Volatile fields — everything that changes between Case-2 updates
    entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    indicators: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    executable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    index_entry_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    lots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sizing_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ai_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_adjustment: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    ai_action: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Timestamp of the signal at that version (its generated_at)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When this snapshot was archived (just before the update overwrote it)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_signal_history_signal_id", "signal_id"),
    )
