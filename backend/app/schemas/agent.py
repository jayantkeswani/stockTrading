import uuid
from datetime import datetime

from pydantic import BaseModel


class AgentStatusResponse(BaseModel):
    running: bool
    yolo_mode: bool = False
    autonomy_level: str = "semi"
    last_action_at: datetime | None = None
    pending_confirmations: int
    positions_monitored: int
    uptime_seconds: int | None = None


class AgentLogResponse(BaseModel):
    id: uuid.UUID
    action_type: str
    trade_id: uuid.UUID | None = None
    details: dict
    requires_confirmation: bool
    confirmation_status: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentConfirmRequest(BaseModel):
    approved: bool


class YoloToggleRequest(BaseModel):
    enabled: bool
