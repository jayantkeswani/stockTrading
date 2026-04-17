"""Tests for market_levels indicator: swing detection and index-level SL/target selection."""

import pytest

from app.core.enums import SignalType
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import CPRResult, CPRType
from app.indicators.market_levels import (
    compute_rr_ratio,
    find_swing_high,
    find_swing_low,
    select_index_sl_target,
)
from app.indicators.open_interest import OIAnalysis
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(o: float, h: float, l: float, c: float, v: int = 1000) -> Candle:
    return Candle(open=o, high=h, low=l, close=c, volume=v)


def _vwap(vwap: float, upper: float, lower: float) -> VWAPResult:
    return VWAPResult(vwap=vwap, upper_band=upper, lower_band=lower)


def _pd(pdh: float, pdl: float, pdc: float) -> PreviousDayLevels:
    return PreviousDayLevels(
        pdh=pdh, pdl=pdl, pdc=pdc, pdo=pdc,
        day_range=pdh - pdl, bias="BULLISH",
    )


def _cpr(pivot: float, tc: float, bc: float, r1: float, s1: float) -> CPRResult:
    return CPRResult(
        pivot=pivot, tc=tc, bc=bc,
        r1=r1, s1=s1, r2=r1 + 100, s2=s1 - 100,
        cpr_type=CPRType.NARROW, cpr_width_pct=0.05,
    )


def _oi(max_pe: float, max_ce: float) -> OIAnalysis:
    return OIAnalysis(
        pcr=1.0, max_ce_oi_strike=max_ce, max_pe_oi_strike=max_pe,
        total_ce_oi=100000, total_pe_oi=100000, max_pain=24000,
        sentiment="NEUTRAL",
    )


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------


class TestFindSwingLow:
    def test_returns_lowest_low(self):
        candles = [
            _candle(100, 105, 95, 102),
            _candle(102, 106, 90, 104),  # lowest low = 90
            _candle(104, 108, 98, 106),
        ]
        assert find_swing_low(candles) == 90

    def test_respects_lookback(self):
        candles = [
            _candle(100, 105, 80, 102),  # outside lookback=2
            _candle(102, 106, 95, 104),
            _candle(104, 108, 98, 106),
        ]
        assert find_swing_low(candles, lookback=2) == 95

    def test_empty_candles(self):
        assert find_swing_low([]) is None

    def test_single_candle(self):
        assert find_swing_low([_candle(100, 105, 95, 102)]) == 95


class TestFindSwingHigh:
    def test_returns_highest_high(self):
        candles = [
            _candle(100, 105, 95, 102),
            _candle(102, 110, 98, 104),  # highest high = 110
            _candle(104, 108, 98, 106),
        ]
        assert find_swing_high(candles) == 110

    def test_respects_lookback(self):
        candles = [
            _candle(100, 120, 95, 102),  # outside lookback=2
            _candle(102, 106, 98, 104),
            _candle(104, 108, 98, 106),
        ]
        assert find_swing_high(candles, lookback=2) == 108

    def test_empty_candles(self):
        assert find_swing_high([]) is None


# ---------------------------------------------------------------------------
# SL/target selection — BUY CE
# ---------------------------------------------------------------------------


class TestSelectIndexSlTargetBuyCE:
    """BUY CE: SL below entry (support), target above entry (resistance)."""

    def test_picks_nearest_support_and_resistance(self):
        # Entry at 24050, min_buffer = 0.10% ≈ 24 points
        # Support candidates: VWAP lower=24020 (30pt away ✓), CPR BC=24010, S1=23950, PDL=23900
        # Target candidates: VWAP upper=24080 (30pt away ✓), CPR R1=24100, PDH=24150
        # CPR TC=24060 is only 10pt away → filtered out by min_buffer
        vwap = _vwap(24050, 24080, 24020)
        pd = _pd(pdh=24150, pdl=23900, pdc=24000)
        cpr = _cpr(pivot=24035, tc=24060, bc=24010, r1=24100, s1=23950)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
            vwap=vwap,
            previous_day=pd,
            cpr=cpr,
        )

        # Nearest valid support: VWAP lower=24020
        assert sl == 24020
        # Nearest valid resistance: VWAP upper=24080 (TC=24060 filtered out)
        assert target == 24080

    def test_filters_levels_too_close(self):
        # Entry at 24050, min_buffer = 0.10% = ~24 points
        # VWAP lower band at 24040 (only 10 points away — too close)
        vwap = _vwap(24050, 24200, 24040)
        pd = _pd(pdh=24200, pdl=23900, pdc=24000)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
            vwap=vwap,
            previous_day=pd,
        )

        # VWAP lower (24040) is too close, falls back to PDL (23900)
        assert sl == 23900

    def test_returns_none_when_no_levels(self):
        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
        )
        assert sl is None
        assert target is None

    def test_returns_none_when_rr_below_1(self):
        # SL far away (big risk), target very close (small reward) → bad R:R
        # Support at 23800 (250 points risk), resistance at 24100 (50 points reward)
        vwap = _vwap(24050, 24100, 23800)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
            vwap=vwap,
        )

        # R:R = 50/250 = 0.2 < 1.0 → both None
        assert sl is None
        assert target is None

    def test_uses_oi_strikes(self):
        oi = _oi(max_pe=24000, max_ce=24150)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
            oi_analysis=oi,
        )

        assert sl == 24000   # max PE OI = support
        assert target == 24150  # max CE OI = resistance

    def test_uses_swing_low(self):
        candles = [_candle(24050, 24060, 24010, 24055) for _ in range(5)]
        pd = _pd(pdh=24200, pdl=23800, pdc=24000)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_CE,
            previous_day=pd,
            candles_5m=candles,
        )

        # Swing low = 24010, PDL = 23800 → nearest is 24010
        assert sl == 24010


# ---------------------------------------------------------------------------
# SL/target selection — BUY PE
# ---------------------------------------------------------------------------


class TestSelectIndexSlTargetBuyPE:
    """BUY PE: SL above entry (resistance), target below entry (support)."""

    def test_picks_nearest_resistance_for_sl_and_support_for_target(self):
        # Entry at 24050
        # SL (resistance above): VWAP upper=24080, PDH=24150
        # Target (support below): VWAP lower=24020, PDL=23900
        vwap = _vwap(24050, 24080, 24020)
        pd = _pd(pdh=24150, pdl=23900, pdc=24000)

        sl, target = select_index_sl_target(
            entry_price=24050,
            signal_type=SignalType.BUY_PE,
            vwap=vwap,
            previous_day=pd,
        )

        # SL = nearest resistance above entry = VWAP upper 24080
        assert sl == 24080
        # Target = nearest support below entry = VWAP lower 24020
        assert target == 24020


# ---------------------------------------------------------------------------
# R:R ratio
# ---------------------------------------------------------------------------


class TestComputeRRRatio:
    def test_buy_ce_1_to_2(self):
        rr = compute_rr_ratio(24050, 24000, 24150, SignalType.BUY_CE)
        assert rr == pytest.approx(2.0)  # 100 reward / 50 risk

    def test_buy_pe_1_to_1_5(self):
        rr = compute_rr_ratio(24050, 24100, 23975, SignalType.BUY_PE)
        assert rr == pytest.approx(1.5)  # 75 reward / 50 risk

    def test_zero_risk_returns_zero(self):
        rr = compute_rr_ratio(24050, 24050, 24100, SignalType.BUY_CE)
        assert rr == 0.0

    def test_negative_risk_returns_zero(self):
        # SL above entry for CE = invalid
        rr = compute_rr_ratio(24050, 24100, 24150, SignalType.BUY_CE)
        assert rr == 0.0
