import uuid
from decimal import Decimal

from pydantic import BaseModel


class YoloProfileCreate(BaseModel):
    name: str
    profit_cap: float
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
    strategies: list[str] = []
    setups: list[str] = []

    model_config = {"from_attributes": True}
