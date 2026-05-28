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


class YoloProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    profit_cap: Decimal
    is_active: bool
    sort_order: int
    is_capped_today: bool = False

    model_config = {"from_attributes": True}
