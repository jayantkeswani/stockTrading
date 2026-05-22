"""Schemas for the trading settings API."""

from pydantic import BaseModel, Field


class TradingConfigResponse(BaseModel):
    capital: int
    max_daily_drawdown_pct: float
    max_daily_profit: float
    max_risk_per_trade_pct: float
    max_trades_per_day: int
    paper_trading: bool
    autonomy_level: str  # "MANUAL" | "SEMI" | "YOLO"
    min_confidence_to_persist: float
    min_confidence_for_shadow: float
    min_confidence_for_execution: float
    shadow_skip_permanent_watchlist: bool
    yolo_skip_permanent_watchlist: bool

    model_config = {"from_attributes": True}


class TradingConfigUpdate(BaseModel):
    capital: int | None = Field(default=None, gt=0)
    max_daily_drawdown_pct: float | None = Field(default=None, gt=0, le=100)
    max_daily_profit: float | None = Field(default=None, ge=0)
    max_risk_per_trade_pct: float | None = Field(default=None, gt=0, le=100)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=20)
    paper_trading: bool | None = None
    autonomy_level: str | None = Field(default=None, pattern="^(MANUAL|SEMI|YOLO)$")
    min_confidence_to_persist: float | None = Field(default=None, ge=0, le=100)
    min_confidence_for_shadow: float | None = Field(default=None, ge=0, le=100)
    min_confidence_for_execution: float | None = Field(default=None, ge=0, le=100)
    shadow_skip_permanent_watchlist: bool | None = None
    yolo_skip_permanent_watchlist: bool | None = None
