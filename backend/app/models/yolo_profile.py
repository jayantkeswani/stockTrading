import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class YoloProfile(Base, TimestampMixin):
    """Configurable profit cap tier for YOLO agent execution."""

    __tablename__ = "yolo_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    profit_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Thesis-invalidation exit (S5 only): when set, the trade monitor closes this
    # profile's open S5 positions early if the live NIFTY intraday bias flips
    # STRONG-opposite to the position direction for `invalidation_persist`
    # consecutive 1m candles. NULL = disabled. See docs/backtest/s5-invalidation-exit-study.md.
    invalidation_persist: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invalidation_quorum: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invalidation_strong_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_yolo_profiles_is_active", "is_active"),
    )
