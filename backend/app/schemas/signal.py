import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class SignalResponse(BaseModel):
    id: uuid.UUID
    strategy_name: str
    symbol: str
    signal_type: str
    strike_price: Decimal
    expiry_date: date
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal | None = None
    confidence: Decimal | None = None
    status: str
    reason: str
    indicators: dict
    executable: bool = True
    blocked_reason: str | None = None
    executed_trade_id: uuid.UUID | None = None
    generated_at: datetime
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}
