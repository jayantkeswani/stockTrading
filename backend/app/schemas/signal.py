import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class SignalResponse(BaseModel):
    id: uuid.UUID
    strategy_name: str
    symbol: str
    signal_type: str
    instrument_type: str = "OPTION"
    strike_price: Decimal
    expiry_date: date
    entry_price: Decimal
    index_entry_price: Decimal | None = None
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
    lots: int | None = None
    quantity: int | None = None
    fyers_option_symbol: str | None = None
    fyers_futures_symbol: str | None = None

    model_config = {"from_attributes": True}


class SignalPreviewResponse(BaseModel):
    signal_id: uuid.UUID
    lots: int
    quantity: int
    lot_size: int
    entry_price: float  # live price at preview time
    stop_loss: float
    target_price: float | None
    capital_at_risk: float
    sizing_meta: dict | None = None


class ExecuteSignalRequest(BaseModel):
    lots: int | None = None  # Optional override; uses signal.lots if omitted
