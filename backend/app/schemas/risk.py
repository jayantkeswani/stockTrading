from decimal import Decimal

from pydantic import BaseModel


class RiskDashboardResponse(BaseModel):
    capital: Decimal
    daily_pnl: Decimal
    closed_pnl: Decimal
    daily_drawdown_pct: float
    max_daily_drawdown_pct: float
    trades_today: int
    max_trades_per_day: int
    notional: Decimal
    risk: Decimal
    margin_utilized: Decimal
    is_halted: bool
    positions_open: int


class MarketStatusResponse(BaseModel):
    is_open: bool
    in_trading_window: bool
    in_dead_zone: bool
    minutes_to_close: int
    india_vix: float | None = None
    cpr_type: str | None = None
    day_bias: str | None = None
    fyers_connected: bool = False
