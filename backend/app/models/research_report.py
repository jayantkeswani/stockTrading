"""Research report models.

ResearchReport: Persisted stock research report with recommendation and full findings.
ResearchAgentRun: Individual sub-agent run within a research session.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, generate_uuid


class ResearchReport(Base, TimestampMixin):
    """Persisted stock research report."""

    __tablename__ = "research_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # Status lifecycle: PENDING -> IN_PROGRESS -> COMPLETED | PARTIAL | FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Report output
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(10), nullable=True)  # BUY/HOLD/SELL/AVOID
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Agent tracking
    agents_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agents_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Price snapshot at research time
    price_at_research: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_cr: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    agent_runs: Mapped[list["ResearchAgentRun"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("idx_research_reports_symbol", "symbol"),
        Index("idx_research_reports_status", "status"),
        Index("idx_research_reports_created", "created_at"),
    )


class ResearchAgentRun(Base, TimestampMixin):
    """Individual sub-agent run within a research session."""

    __tablename__ = "research_agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(30), nullable=False)

    # Status lifecycle: PENDING -> RUNNING -> COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Output
    findings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_sources_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Relationship
    report: Mapped["ResearchReport"] = relationship(back_populates="agent_runs")

    __table_args__ = (
        Index("idx_research_agent_runs_report", "report_id"),
    )
