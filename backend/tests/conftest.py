"""Shared pytest fixtures and factory functions for the test suite.

These helpers build MarketContext and indicator objects with sensible defaults.
Used by both unit tests and backtest replay tests.
"""

import pytest

from app.core.enums import CPRType, DayBias
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import CPRResult
from app.indicators.global_market import GlobalCues
from app.indicators.open_interest import OIAnalysis
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult
from app.strategies.base import MarketContext


# ---------------------------------------------------------------------------
# Indicator factories
# ---------------------------------------------------------------------------

def make_vwap(vwap: float = 100.0) -> VWAPResult:
    return VWAPResult(vwap=vwap, upper_band=vwap + 1, lower_band=vwap - 1)


def make_prev_day(bias: DayBias = DayBias.BULLISH) -> PreviousDayLevels:
    return PreviousDayLevels(
        pdh=110, pdl=90, pdc=105, pdo=100, day_range=20, bias=bias,
    )


def make_cpr(cpr_type: CPRType = CPRType.WIDE) -> CPRResult:
    return CPRResult(
        pivot=100, tc=101, bc=99, r1=110, s1=90, r2=120, s2=80,
        cpr_type=cpr_type, cpr_width_pct=2.0 if cpr_type == CPRType.WIDE else 0.05,
    )


def make_oi(
    pcr: float = 1.0,
    max_pe_strike: float = 95.0,
    max_ce_strike: float = 105.0,
    sentiment: str = "NEUTRAL",
) -> OIAnalysis:
    return OIAnalysis(
        pcr=pcr,
        max_ce_oi_strike=max_ce_strike,
        max_pe_oi_strike=max_pe_strike,
        total_ce_oi=10000,
        total_pe_oi=int(10000 * pcr),
        max_pain=100,
        sentiment=sentiment,
    )


def make_global_cues(
    dow_futures_pct: float = 0.3,
    sp500_close_pct: float = 0.2,
    us_vix: float = 14.0,
) -> GlobalCues:
    cues = GlobalCues(
        dow_futures_pct=dow_futures_pct,
        sp500_close_pct=sp500_close_pct,
        us_vix=us_vix,
    )
    from app.indicators.global_market import combined_global_score
    cues.global_score = combined_global_score(cues)
    return cues


# ---------------------------------------------------------------------------
# Candle pattern factories
# ---------------------------------------------------------------------------

def bullish_engulfing_candles(price: float = 100.0) -> list[Candle]:
    """5+ candles where the last two form a bullish engulfing at the given price."""
    base = [
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price + 0.5, volume=100),
    ]
    prev = Candle(open=price + 1, high=price + 1.5, low=price - 0.5, close=price - 0.3, volume=100)
    curr = Candle(open=price - 0.5, high=price + 2, low=price - 1, close=price + 1.5, volume=100)
    return base + [prev, curr]


def bearish_engulfing_candles(price: float = 100.0) -> list[Candle]:
    """5+ candles where the last two form a bearish engulfing at the given price."""
    base = [
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
        Candle(open=price, high=price + 1, low=price - 1, close=price - 0.5, volume=100),
    ]
    prev = Candle(open=price - 0.3, high=price + 1, low=price - 0.5, close=price + 1, volume=100)
    curr = Candle(open=price + 1.5, high=price + 2, low=price - 1, close=price - 0.5, volume=100)
    return base + [prev, curr]


def flat_candles(price: float = 100.0, n: int = 6) -> list[Candle]:
    """n identical neutral candles (no pattern)."""
    return [
        Candle(open=price, high=price + 0.5, low=price - 0.5, close=price, volume=100)
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# MarketContext factory
# ---------------------------------------------------------------------------

def make_context(
    price: float = 100.05,
    vwap_val: float = 100.0,
    bias: DayBias = DayBias.BULLISH,
    cpr_type: CPRType = CPRType.WIDE,
    candles: list[Candle] | None = None,
    oi: OIAnalysis | None = None,
    india_vix: float | None = 16.0,
    global_cues: GlobalCues | None = None,
    symbol: str = "NIFTY",
    current_time_ist: str = "10:30:00",
    vwap: VWAPResult | None = ...,         # type: ignore[assignment]
    prev_day: PreviousDayLevels | None = ...,  # type: ignore[assignment]
    cpr: CPRResult | None = ...,           # type: ignore[assignment]
) -> MarketContext:
    """Build a MarketContext with sensible defaults.

    Pass ``...`` (Ellipsis) to auto-create an indicator; pass ``None`` to omit it.
    """
    if vwap is ...:
        vwap = make_vwap(vwap_val)
    if prev_day is ...:
        prev_day = make_prev_day(bias)
    if cpr is ...:
        cpr = make_cpr(cpr_type)
    if candles is None:
        candles = bullish_engulfing_candles(price)

    return MarketContext(
        symbol=symbol,
        current_price=price,
        candles_5m=candles,
        vwap=vwap,
        previous_day=prev_day,
        cpr=cpr,
        oi_analysis=oi,
        india_vix=india_vix,
        current_time_ist=current_time_ist,
        global_cues=global_cues,
    )
