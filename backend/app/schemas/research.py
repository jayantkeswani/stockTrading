"""Pydantic schemas for research endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


# ── Requests ──


class ResearchStartRequest(BaseModel):
    symbol: str


# ── Responses ──


class ResearchStartResponse(BaseModel):
    report_id: uuid.UUID
    symbol: str
    display_name: str
    status: str
    agents_total: int


class AgentRunResponse(BaseModel):
    agent_name: str
    status: str
    summary_text: str | None = None
    findings_json: dict | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    data_sources_used: list[str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ResearchReportResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    display_name: str
    status: str
    recommendation: str | None = None
    confidence_score: float | None = None
    executive_summary: str | None = None
    report_json: dict | None = None
    report_markdown: str | None = None
    agents_completed: int
    agents_total: int
    price_at_research: float | None = None
    market_cap_cr: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    agent_runs: list[AgentRunResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ResearchReportListItem(BaseModel):
    id: uuid.UUID
    symbol: str
    display_name: str
    status: str
    recommendation: str | None = None
    confidence_score: float | None = None
    executive_summary: str | None = None
    price_at_research: float | None = None
    duration_seconds: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
