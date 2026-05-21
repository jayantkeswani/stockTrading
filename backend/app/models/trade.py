import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


def build_signal_snapshot(signal) -> dict:
    return {
        "confidence": float(signal.confidence) if signal.confidence else None,
        "reason": signal.reason,
        "indicators": signal.indicators,
        "ai_summary": signal.ai_summary,
        "ai_rationale": signal.ai_rationale,
        "ai_adjustment": float(signal.ai_adjustment) if signal.ai_adjustment else None,
        "ai_action": signal.ai_action,
        "generated_at": signal.generated_at.isoformat() if signal.generated_at else None,
        "sizing_meta": signal.sizing_meta,
        "entry_price": float(signal.entry_price) if signal.entry_price else None,
        "stop_loss": float(signal.stop_loss) if signal.stop_loss else None,
        "target_price": float(signal.target_price) if signal.target_price else None,
        "index_entry_price": float(signal.index_entry_price) if signal.index_entry_price else None,
        "instrument_type": signal.instrument_type,
        "signal_type": signal.signal_type,
        "lots": signal.lots,
        "quantity": signal.quantity,
    }


class Trade(Base, TimestampMixin):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    strike_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    option_type: Mapped[str | None] = mapped_column(String(5), nullable=True)  # CE, PE, or None for futures
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
    position_type: Mapped[str] = mapped_column(
        String(15), nullable=False, default="INTRADAY"
    )  # INTRADAY or POSITIONAL
    fyers_option_symbol: Mapped[str | None] = mapped_column(String(60), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    charges_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_permanent_watchlist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signal_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    signal_ai_action: Mapped[str | None] = mapped_column(String(30), nullable=True)
    signal_ai_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    signal_instrument_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    signal_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    signal_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_trades_status", "status"),
        Index("idx_trades_entry_time", "entry_time"),
        Index("idx_trades_strategy", "strategy_name"),
        Index("idx_trades_source", "source"),
    )
