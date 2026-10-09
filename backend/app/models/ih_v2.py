"""Intraday Hunter v2 learning-loop tables.

- `IhMinuteLog`   — the learning dataset: one row per (date, minute, index, variant) with the
                    minute's features + what every candidate arm would decide.
- `IhTeacherDay`  — the real trader's evening plan + his actual live trade, per trading date.
- `IhDayGrade`    — nightly grade per trading date: market labels, teacher, v1/v2, every arm's
                    counterfactual basket P&L on real premiums, gate what-ifs, the lesson.
- `IhWeeklyReview`— Saturday proposal (evidence + suggested param/prompt changes). Never applied.
- `IhV2Proposal`  — one row per proposal in a weekly review: the per-proposal approval status
                    chain, the apply agent's plan, and (once APPLIED) the challenger it runs as.

Design spec: docs/ai/intraday-hunter-v2.md §Data model.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class IhMinuteLog(Base, TimestampMixin):
    """One learning-dataset row per (trading_date, minute_ts, index, variant)."""

    __tablename__ = "ih_minute_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    minute_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    index: Mapped[str] = mapped_column("index", String(20), nullable=False)
    variant: Mapped[str] = mapped_column(String(10), nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    arms: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("trading_date", "minute_ts", "index", "variant", name="uq_ih_minute_log"),
        Index("idx_ih_minute_log_date", "trading_date"),
    )


class IhTeacherDay(Base, TimestampMixin):
    """The teacher's (@IntradayHunter) plan + actual live trade for one trading date.

    status: PENDING | PLAN_READY | PLAN_MISSING | LIVE_READY | LIVE_MISSING (latest stage).
    `errors` is an append-only list of {job, at, error_type, detail}.
    """

    __tablename__ = "ih_teacher_days"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    plan_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    live_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    live_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # server | mac_push
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class IhDayGrade(Base, TimestampMixin):
    """Nightly grade for one trading date (PRELIM at 16:00, FINAL once the teacher live lands)."""

    __tablename__ = "ih_day_grades"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'PRELIM'"))
    market: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    teacher: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    v1: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    v2: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    arms: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    gates: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    lesson: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IhWeeklyReview(Base, TimestampMixin):
    """Saturday weekly review: Claude's evidence-backed proposal. NOTHING is auto-applied."""

    __tablename__ = "ih_weekly_reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    week_ending: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    ledger: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    proposal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    calibration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'PROPOSED'"))


class IhV2Proposal(Base, TimestampMixin):
    """One weekly-review proposal and its approval lifecycle (never applied without a click).

    Status chain: PROPOSED → APPROVED | REJECTED → ANALYSED | NEEDS_REVIEW → APPLIED →
    PROMOTED | RETIRED. A `code` change stops at ANALYSED with a build brief. Once APPLIED the
    proposal IS a challenger (`challenger_id` c1, c2…): `counterfactual` mode re-scores v2's own
    decision with `params_override` in the nightly grade; `shadow_call2` mode also runs its own
    Call 2 (run rows variant `v2{challenger_id}`, no orders) — both appear as ledger arm
    `ch_{challenger_id}`. Lifecycle: services/intraday_hunter_v2/apply.py.
    """

    __tablename__ = "ih_v2_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ih_weekly_reviews.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(14), nullable=False, server_default=text("'PROPOSED'"))
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    challenger_id: Mapped[str | None] = mapped_column(String(8), nullable=True, unique=True)
    challenger_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    params_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    history: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        UniqueConstraint("review_id", "idx", name="uq_ih_v2_proposals_review_idx"),
        Index("idx_ih_v2_proposals_status", "status"),
    )
