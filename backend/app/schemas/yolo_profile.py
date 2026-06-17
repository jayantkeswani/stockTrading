import uuid
from decimal import Decimal

from pydantic import BaseModel


class YoloProfileCreate(BaseModel):
    name: str
    profit_cap: float
    # Per-profile YOLO execution-confidence threshold (None = inherit the global default).
    min_confidence_for_execution: float | None = None
    # Per-profile intraday-bias gate (None = no gate; "WEAK"/"MODERATE"/"STRONG").
    min_bias_strength: str | None = None
    # Opt-in positive-magnitude gates (None/<=0 = off): ADR% floor, daily loss cap, per-lot stop.
    min_adr: float | None = None
    loss_cap: float | None = None
    per_lot_loss_stop: float | None = None
    # Execution-side filters (empty = act on all signals).
    strategies: list[str] = []
    setups: list[str] = []


class YoloProfileUpdate(BaseModel):
    name: str | None = None
    profit_cap: float | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    # Thesis-invalidation exit (S5/S6 momentum). invalidation_persist=null disables it;
    # a positive int enables the exit at that many consecutive opposing candles.
    invalidation_persist: int | None = None
    invalidation_quorum: bool | None = None
    invalidation_strong_only: bool | None = None
    # Per-profile YOLO execution-confidence threshold (0-100). NOTE: the PATCH endpoint drops
    # None via exclude_none, so send a NEGATIVE value (e.g. -1) to CLEAR back to "inherit global".
    min_confidence_for_execution: float | None = None
    # Per-profile intraday-bias gate (WEAK/MODERATE/STRONG). NOTE: the PATCH endpoint drops None
    # via exclude_none, so send an empty string "" to CLEAR the gate back to "no gate".
    min_bias_strength: str | None = None
    # Opt-in positive-magnitude gates: ADR% floor, daily loss cap, per-lot MTM loss stop. NOTE:
    # the PATCH endpoint drops None via exclude_none, so send 0 to CLEAR back to "off".
    min_adr: float | None = None
    loss_cap: float | None = None
    per_lot_loss_stop: float | None = None
    # Execution-side filters. NOTE: the PATCH endpoint drops None via exclude_none, so
    # send an empty list `[]` (not null) to CLEAR a filter back to "act on all".
    strategies: list[str] | None = None
    setups: list[str] | None = None


class YoloProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    profit_cap: Decimal
    is_active: bool
    sort_order: int
    is_capped_today: bool = False
    invalidation_persist: int | None = None
    invalidation_quorum: bool = False
    invalidation_strong_only: bool = True
    min_confidence_for_execution: float | None = None
    min_bias_strength: str | None = None
    min_adr: float | None = None
    loss_cap: float | None = None
    per_lot_loss_stop: float | None = None
    strategies: list[str] = []
    setups: list[str] = []

    model_config = {"from_attributes": True}
