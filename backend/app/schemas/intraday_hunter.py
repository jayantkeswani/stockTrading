"""Pydantic schemas for the Intraday Hunter agent API.

The Call 1 / Call 2 payloads are free-form (the LLM JSON), surfaced as `dict`. Chart images
are exposed as URLs onto `GET /chart/{run_id}/{which}` rather than filesystem paths.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel

_API_BASE = "/api/v1/intraday-hunter"


def _chart_urls(run) -> dict:
    """Build {prevday: {index: url}, opening: {index: url}} from the run's stored paths.

    Prefers the Call 2 prev-day charts (re-rendered each decision); falls back to Call 1's.
    """
    out: dict = {"prevday": {}, "opening": {}}
    c2 = run.call2_chart_paths or {}
    prevday_src = (c2.get("prevday") or {}) or (run.call1_chart_paths or {})
    for idx in prevday_src:
        out["prevday"][idx] = f"{_API_BASE}/chart/{run.id}/prevday_{idx}"
    for idx in (c2.get("opening") or {}):
        out["opening"][idx] = f"{_API_BASE}/chart/{run.id}/opening_{idx}"
    return out


class IntradayHunterRunResponse(BaseModel):
    """Full run row for the /today view (thesis + latest decision + chart URLs)."""

    id: uuid.UUID | None = None
    trading_date: date
    status: str
    is_expiry: bool = False
    expiry_index: str | None = None
    call1_json: dict | None = None
    call2_json: dict | None = None
    call2_history: list = []
    decision: str | None = None
    direction: str | None = None
    confidence: int | None = None
    outcome_played_out: bool | None = None
    realized_outcome_note: str | None = None
    chart_urls: dict = {"prevday": {}, "opening": {}}
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_run(cls, run) -> "IntradayHunterRunResponse":
        return cls(
            id=run.id,
            trading_date=run.trading_date,
            status=run.status,
            is_expiry=run.is_expiry,
            expiry_index=run.expiry_index,
            call1_json=run.call1_json,
            call2_json=run.call2_json,
            call2_history=run.call2_history or [],
            decision=run.decision,
            direction=run.direction,
            confidence=run.confidence,
            outcome_played_out=run.outcome_played_out,
            realized_outcome_note=run.realized_outcome_note,
            chart_urls=_chart_urls(run),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @classmethod
    def pending_stub(cls, trading_date: date) -> "IntradayHunterRunResponse":
        """A transient PENDING response for a day with no row yet (e.g. before 08:45)."""
        return cls(trading_date=trading_date, status="PENDING")


class IntradayHunterBasketLeg(BaseModel):
    """One live/closed Intraday Hunter basket leg, for the page's manual close card.

    Surfaces each leg's index spot vs its index_sl/index_target as informational context —
    the monitor actually exits each leg on its own option-premium stop_loss/target_price via
    the normal per-position check. `position_id` is set for OPEN legs (so the UI can close
    them); closed-today legs carry the realized P&L + exit reason.
    """

    position_id: uuid.UUID | None = None
    trade_id: uuid.UUID | None = None
    book: str  # "SHADOW" or the YOLO profile name
    is_shadow: bool = False
    index: str
    option_type: str | None = None
    strike: float | None = None
    itm_depth: int | None = None
    fyers_option_symbol: str | None = None
    lots: int | None = None
    entry_price: float | None = None
    current_price: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    status: str  # "OPEN" | "CLOSED"
    exit_reason: str | None = None
    index_sl: float | None = None
    index_target: float | None = None
    index_spot: float | None = None


class IntradayHunterHistoryItem(BaseModel):
    """Compact prior-day row for the history timeline."""

    trading_date: date
    status: str
    decision: str | None = None
    direction: str | None = None
    confidence: int | None = None
    is_expiry: bool = False
    expiry_index: str | None = None
    trapped_side: str | None = None
    thesis: str | None = None
    outcome_played_out: bool | None = None
    realized_outcome_note: str | None = None

    @classmethod
    def from_run(cls, run) -> "IntradayHunterHistoryItem":
        src = run.call2_json or run.call1_json or {}
        return cls(
            trading_date=run.trading_date,
            status=run.status,
            decision=run.decision,
            direction=run.direction,
            confidence=run.confidence,
            is_expiry=run.is_expiry,
            expiry_index=run.expiry_index,
            trapped_side=src.get("trapped_side") if isinstance(src, dict) else None,
            thesis=src.get("thesis") if isinstance(src, dict) else None,
            outcome_played_out=run.outcome_played_out,
            realized_outcome_note=run.realized_outcome_note,
        )
