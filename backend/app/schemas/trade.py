import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class TradeResponse(BaseModel):
    id: uuid.UUID
    signal_id: uuid.UUID | None = None
    strategy_name: str
    symbol: str
    expiry_date: date
    strike_price: Decimal
    option_type: str | None = None
    side: str
    quantity: int
    lots: int
    entry_price: Decimal
    exit_price: Decimal | None = None
    stop_loss: Decimal
    target_price: Decimal | None = None
    status: str
    exit_reason: str | None = None
    is_paper: bool
    source: str = "MANUAL"
    pnl: Decimal | None = None
    pnl_percent: Decimal | None = None
    entry_time: datetime
    exit_time: datetime | None = None
    notes: str | None = None
    created_at: datetime
    # Signal simulation fields (populated via LEFT JOIN on signal_id)
    signal_confidence: Decimal | None = None
    signal_ai_action: str | None = None
    signal_ai_summary: str | None = None
    signal_instrument_type: str | None = None
    signal_type: str | None = None

    model_config = {"from_attributes": True}


class TradeCloseRequest(BaseModel):
    exit_price: Decimal | None = None
    reason: str = "MANUAL"


class TradeSummaryResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: Decimal
    avg_pnl: Decimal
    avg_winner: Decimal
    avg_loser: Decimal
    best_trade: Decimal
    worst_trade: Decimal
    profit_factor: float
