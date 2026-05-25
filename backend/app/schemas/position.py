import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class PositionResponse(BaseModel):
    id: uuid.UUID
    trade_id: uuid.UUID
    symbol: str
    strike_price: Decimal
    option_type: str
    expiry_date: date
    lots: int
    quantity: int
    entry_price: Decimal
    current_price: Decimal | None = None
    stop_loss: Decimal
    target_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    fyers_option_symbol: str | None = None
    strategy_name: str
    is_paper: bool
    is_shadow: bool = False
    position_type: str = "INTRADAY"
    opened_at: datetime
    signal_generated_at: datetime | None = None
    signal_confidence: Decimal | None = None
    margin_required: Decimal | None = None
    is_permanent_watchlist: bool = False

    model_config = {"from_attributes": True}


class PositionCloseRequest(BaseModel):
    reason: str = "MANUAL"


class PositionUpdateSLRequest(BaseModel):
    stop_loss: Decimal
