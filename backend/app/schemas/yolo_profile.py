import uuid
from decimal import Decimal

from pydantic import BaseModel


class YoloProfileCreate(BaseModel):
    name: str
    profit_cap: float


class YoloProfileUpdate(BaseModel):
    name: str | None = None
    profit_cap: float | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    # Thesis-invalidation exit (S5 only). invalidation_persist=null disables it;
    # a positive int enables the exit at that many consecutive opposing candles.
    invalidation_persist: int | None = None
    invalidation_quorum: bool | None = None
    invalidation_strong_only: bool | None = None


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

    model_config = {"from_attributes": True}
