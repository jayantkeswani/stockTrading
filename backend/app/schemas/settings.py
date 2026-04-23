"""Schemas for the trading settings API."""

from pydantic import BaseModel, Field


class TradingConfigResponse(BaseModel):
    capital: int
    max_daily_drawdown_pct: float
    max_risk_per_trade_pct: float
    max_trades_per_day: int
    paper_trading: bool
    autonomy_level: str  # "MANUAL" | "SEMI" | "YOLO"

    model_config = {"from_attributes": True}


class TradingConfigUpdate(BaseModel):
    capital: int | None = Field(default=None, gt=0)
    max_daily_drawdown_pct: float | None = Field(default=None, gt=0, le=100)
    max_risk_per_trade_pct: float | None = Field(default=None, gt=0, le=100)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=20)
    paper_trading: bool | None = None
    autonomy_level: str | None = Field(default=None, pattern="^(MANUAL|SEMI|YOLO)$")
