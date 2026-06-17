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

    # Per-profile minimum intraday-bias strength gate. NULL = no gate (any bias).
    # When set ("WEAK"/"MODERATE"/"STRONG") the profile only executes a signal whose stored
    # stock intraday_bias.strength is >= this (ordinal WEAK<MODERATE<STRONG); a signal with
    # no bias object is rejected. Only S5 (intraday_futures) signals carry intraday_bias, so
    # this is effectively S5-scoped. Promotes the bias from a soft confidence factor into a
    # hard precondition for one book (the validated S5 PDH_PDL/ORB + STRONG-bias setup).
    min_bias_strength: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Per-profile minimum ADR% execution gate. NULL = no filter. When set, the profile only
    # executes a signal whose stored indicators.adr_pct is >= this. The universe backtest
    # found the PDH_PDL edge concentrated above an ~2.8% ADR cutoff (below it a net loser),
    # so this promotes ADR from a soft screener factor into a hard execution precondition.
    min_adr: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Per-profile DAILY loss cap (positive INR magnitude). NULL/0 = no cap. The symmetric twin
    # of profit_cap: when this profile's net P&L (realized + bookable unrealized, after charges)
    # falls to <= -loss_cap, the trade monitor closes all of this profile's open positions
    # (ExitReason.LOSS_CAP) and blocks further YOLO executions for the rest of the day. Distinct
    # from the global trading_config drawdown gate (which is shared across all profiles).
    loss_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Per-profile per-LOT MTM loss stop (positive INR magnitude per lot). NULL/0 = disabled.
    # A hard money stop checked per open position: when a position's unrealized loss per lot
    # reaches this, the trade monitor closes that single position (ExitReason.PER_LOT_STOP) —
    # it can fire before the structural SL. Validated as S5-futures tail insurance (options are
    # effectively immune since they can't lose more than the premium paid).
    per_lot_loss_stop: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

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
