from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class CandleResponse(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class PriceResponse(BaseModel):
    symbol: str
    ltp: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    timestamp: datetime


class IndicatorResponse(BaseModel):
    symbol: str
    vwap: Decimal | None = None
    pdh: Decimal | None = None  # Previous day high
    pdl: Decimal | None = None  # Previous day low
    pdc: Decimal | None = None  # Previous day close
    cpr_tc: Decimal | None = None  # Top Central
    cpr_bc: Decimal | None = None  # Bottom Central
    cpr_pivot: Decimal | None = None
    cpr_type: str | None = None  # NARROW, WIDE
    day_bias: str | None = None  # BULLISH, BEARISH, NEUTRAL
    india_vix: Decimal | None = None
    pcr: Decimal | None = None
    timestamp: datetime


class OIDataResponse(BaseModel):
    symbol: str
    expiry_date: str
    strikes: list[dict]
    total_ce_oi: int
    total_pe_oi: int
    pcr: float
    max_ce_oi_strike: float
    max_pe_oi_strike: float
    timestamp: datetime
