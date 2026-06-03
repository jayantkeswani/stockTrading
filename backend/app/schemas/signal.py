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
    fyers_option_symbol: str | None = None
    fyers_futures_symbol: str | None = None
    # Phase 2 LLM overlay
    ai_summary: str | None = None
    ai_rationale: str | None = None
    ai_adjustment: Decimal | None = None
    ai_action: str | None = None
    is_permanent_watchlist: bool = False
    update_count: int = 0  # number of signal_history versions (>0 ⇒ signal was deduped/revised)

    model_config = {"from_attributes": True}


class SignalPreviewResponse(BaseModel):
    signal_id: uuid.UUID
    lots: int
    quantity: int
    lot_size: int
    entry_price: float  # live price at preview time
    stop_loss: float
    target_price: float | None
    risk: float
    notional: float
    margin_required: float
    sizing_meta: dict | None = None
    warnings: list[str] = []


class SignalHistoryResponse(BaseModel):
    id: uuid.UUID
    signal_id: uuid.UUID
    version: int
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal | None = None
    confidence: Decimal | None = None
    reason: str
    indicators: dict
    executable: bool
    blocked_reason: str | None = None
    index_entry_price: Decimal | None = None
    ai_summary: str | None = None
    ai_rationale: str | None = None
    ai_adjustment: Decimal | None = None
    ai_action: str | None = None
    generated_at: datetime
    captured_at: datetime

    model_config = {"from_attributes": True}


class ExecuteSignalRequest(BaseModel):
    lots: int | None = None  # Optional lots override for manual execution
