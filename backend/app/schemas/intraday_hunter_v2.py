"""Pydantic schemas for the Intraday Hunter v2 API (v2 run, basket, teacher, grades, ledger).

v2 run responses reuse v1's `IntradayHunterRunResponse` / `IntradayHunterHistoryItem` (same row
shape, `variant='v2'`). LLM / grading payloads are free-form JSONB surfaced as `dict`.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class IhV2Leg(BaseModel):
    """One v2 basket leg (open or closed today)."""

    position_id: uuid.UUID | None = None
    trade_id: uuid.UUID | None = None
    index: str
    option_type: str | None = None
    strike: float | None = None
    leg: str | None = None  # ATM / OTM-1
    fyers_option_symbol: str | None = None
    lots: int | None = None
    quantity: int | None = None
    entry_price: float | None = None
    ltp: float | None = None
    bid: float | None = None
    pnl: float | None = None  # open: bid-valued unrealized; closed: realized net
    status: str  # OPEN | CLOSED
    exit_reason: str | None = None


class IhV2Book(BaseModel):
    """One basket (YOLO profile or SHADOW) with basket-level MTM vs its ±T band."""

    book: str  # 'SHADOW' or the YOLO profile name
    is_shadow: bool
    direction: str | None = None
    status: str  # OPEN | CLOSED
    cost: float
    mtm: float | None = None  # bid-valued (open) / realized net (closed)
    T: float
    pct_of_T: float | None = None
    round_hold_active: bool = False
    round_hold_targets: dict = {}
    exit_reason: str | None = None
    legs: list[IhV2Leg]


class IhV2BasketResponse(BaseModel):
    trading_date: date
    basket_tp_sl_pct: float
    time_exit: str
    books: list[IhV2Book]


class IhTeacherDayResponse(BaseModel):
    trading_date: date
    status: str
    plan: dict | None = None
    plan_video_id: str | None = None
    plan_fetched_at: datetime | None = None
    live: dict | None = None
    live_video_id: str | None = None
    live_fetched_at: datetime | None = None
    source: str | None = None
    errors: list = []

    @classmethod
    def from_row(cls, r) -> "IhTeacherDayResponse":
        return cls(trading_date=r.trading_date, status=r.status, plan=r.plan,
                   plan_video_id=r.plan_video_id, plan_fetched_at=r.plan_fetched_at,
                   live=r.live, live_video_id=r.live_video_id, live_fetched_at=r.live_fetched_at,
                   source=r.source, errors=r.errors or [])


class IhTeacherIngestRequest(BaseModel):
    """Push from the user's Mac (scripts/intraday_hunter/teacher_ingest_local.py --push)."""

    trading_date: date
    plan: dict | None = None
    plan_video_id: str | None = Field(None, max_length=32)  # ih_teacher_days varchar(32)
    live: dict | None = None
    live_video_id: str | None = Field(None, max_length=32)


class IhDayGradeResponse(BaseModel):
    trading_date: date
    status: str
    market: dict = {}
    teacher: dict | None = None
    v1: dict | None = None
    v2: dict | None = None
    arms: dict = {}
    gates: dict = {}
    lesson: dict | None = None
    graded_at: datetime | None = None

    @classmethod
    def from_row(cls, r) -> "IhDayGradeResponse":
        return cls(trading_date=r.trading_date, status=r.status, market=r.market or {},
                   teacher=r.teacher, v1=r.v1, v2=r.v2, arms=r.arms or {}, gates=r.gates or {},
                   lesson=r.lesson, graded_at=r.graded_at)


class IhWeeklyReviewResponse(BaseModel):
    id: uuid.UUID
    week_ending: date
    status: str
    summary: str | None = None
    proposal: dict | None = None
    calibration: dict | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, r) -> "IhWeeklyReviewResponse":
        return cls(id=r.id, week_ending=r.week_ending, status=r.status, summary=r.summary,
                   proposal=r.proposal, calibration=r.calibration, created_at=r.created_at)
