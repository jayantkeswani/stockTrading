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
    charges_json: dict | None = None
    net_pnl: Decimal | None = None
    margin_required: Decimal | None = None
    entry_time: datetime
    exit_time: datetime | None = None
    notes: str | None = None
    created_at: datetime
    # Signal snapshot — populated from Trade columns (snapshotted at execution time)
    signal_confidence: Decimal | None = None
    signal_ai_action: str | None = None
    signal_ai_summary: str | None = None
    signal_instrument_type: str | None = None
    signal_type: str | None = None
    signal_snapshot: dict | None = None
    signal_is_permanent_watchlist: bool | None = None

    model_config = {"from_attributes": True}


class MarginAnalysisRequest(BaseModel):
    trade_ids: list[uuid.UUID]


class MarginAnalysisResponse(BaseModel):
    peak_margin: Decimal
    peak_time: datetime | None = None
    total_margin: Decimal
    trade_count: int


class HoldAnalysisRequest(BaseModel):
    trade_ids: list[uuid.UUID]
    scenario: str  # "best" or "worst"


class PerTradeHoldResult(BaseModel):
    trade_id: uuid.UUID
    max_high: Decimal | None = None
    min_low: Decimal | None = None
    hold_pnl: Decimal | None = None
    hold_net_pnl: Decimal | None = None
    hold_charges_json: dict | None = None
    hold_exit_time: datetime | None = None
    data_found: bool = False


class HoldAnalysisResponse(BaseModel):
    results: list[PerTradeHoldResult]


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
    total_net_pnl: Decimal = Decimal(0)
    total_charges: Decimal = Decimal(0)
