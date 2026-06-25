import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class IntradayHunterRun(Base, TimestampMixin):
    """One row per trading day for the Intraday Hunter agent.

    Updated in place as the day progresses: PENDING -> THESIS_READY (Call 1 done) ->
    WATCHING (open) -> ENTER/WAIT/SKIP (latest Call 2 decision). Stores the full Call 1
    thesis, every Call 2 decision (latest + audit history), the rendered chart paths, and
    a denormalized decision/direction/confidence for fast list queries. `outcome_played_out`
    is filled post-hoc (structural, for the multi-day memory snapshot fed to Call 1);
    `realized_outcome_note` is a human-facing P&L/outcome note shown only on the UI history —
    it is NEVER fed back into a prompt (avoids revenge/timidity framing).

    Design spec: docs/ai/intraday-hunter-agent.md §Data model.
    """

    __tablename__ = "intraday_hunter_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)

    # PENDING / THESIS_READY / WATCHING / ENTER / WAIT / SKIP
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )

    is_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    expiry_index: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Call 1 (pre-open thesis) output JSON + rendered prev-day chart paths (list).
    call1_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    call1_chart_paths: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Latest Call 2 (decision) output JSON; the full audit array of every Call 2 that day;
    # the prev-day + opening chart paths for the latest Call 2.
    call2_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    call2_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    call2_chart_paths: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Denormalized from the latest Call 2 for fast list queries.
    decision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Filled post-hoc. Structural (for the prompt memory snapshot) vs human-facing (UI only).
    outcome_played_out: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    realized_outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_intraday_hunter_runs_trading_date", "trading_date"),
    )
