import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
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

    # Per-profile YOLO execution-confidence threshold. NULL = inherit the global
    # trading_config.min_confidence_for_execution. Lets one profile (e.g. a vwap_reclaim
    # tier) run a lower bar than another (e.g. the S2 tier) from one signal stream.
    min_confidence_for_execution: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # Execution-side filters. A profile only executes a signal when
    # (strategies empty OR signal.strategy_name in strategies) AND
    # (setups empty OR signal.indicators.setup_type in setups). Empty list = all
    # (backward compatible). Lets one paper book run a full-vs-subset A/B from a
    # single signal stream (e.g. an S6 full profile beside an S6 ORB_RETEST-only one).
    strategies: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    setups: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        Index("idx_yolo_profiles_is_active", "is_active"),
    )
