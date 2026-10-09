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
    basket_t_mode: str = "pct"  # "pct" (T = pct x cost) | "rupees" (T = lots x rupees_per_lot)
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


class IhV2ProposalResponse(BaseModel):
    """One weekly-review proposal with its lifecycle, apply plan and challenger stats."""

    id: uuid.UUID
    review_id: uuid.UUID
    week_ending: date | None = None
    idx: int
    change: str
    kind: str | None = None
    evidence: str | None = None
    expected_effect: str | None = None
    risk: str | None = None
    status: str
    user_note: str | None = None
    apply_plan: dict | None = None
    challenger_id: str | None = None
    challenger_mode: str | None = None  # counterfactual | shadow_call2
    params_override: dict | None = None
    started_on: date | None = None
    ended_on: date | None = None
    history: list = []
    stats: dict | None = None  # apply.challenger_stats (challengers only)
    actions: list[str] = []    # buttons the UI may offer now (apply.allowed_actions)

    @classmethod
    def from_row(cls, r, *, week_ending: date | None = None, stats: dict | None = None,
                 actions: list[str] | None = None) -> "IhV2ProposalResponse":
        return cls(id=r.id, review_id=r.review_id, week_ending=week_ending, idx=r.idx, change=r.change,
                   kind=r.kind, evidence=r.evidence, expected_effect=r.expected_effect, risk=r.risk,
                   status=r.status, user_note=r.user_note, apply_plan=r.apply_plan,
                   challenger_id=r.challenger_id, challenger_mode=r.challenger_mode,
                   params_override=r.params_override, started_on=r.started_on, ended_on=r.ended_on,
                   history=r.history or [], stats=stats, actions=actions or [])


class IhV2ProposalAction(BaseModel):
    """Body for approve / reject / analyse / promote / retire."""

    note: str | None = Field(None, max_length=2000)
