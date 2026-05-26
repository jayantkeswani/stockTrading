"""Integration test: VWAP strategy emits index-level SL/target,
option_resolver converts them to premium-level prices via delta.

This validates the full pipeline end-to-end with mock data.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.core.enums import DayBias, SignalType, InstrumentType
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import CPRResult, CPRType
from app.indicators.open_interest import OIAnalysis
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import VWAPResult
from app.strategies.base import MarketContext
from app.strategies.strategy_2_vwap_pullback import VWAPPullbackStrategy


def _make_bullish_candles(base_price: float, count: int = 10) -> list[Candle]:
    """Generate candles with a bullish reversal at the end (bullish engulfing)."""
    candles = []
    for i in range(count - 2):
        candles.append(Candle(
            open=base_price, high=base_price + 5,
            low=base_price - 5, close=base_price + 2,
            volume=500,
        ))
    # Second-to-last: bearish candle
    candles.append(Candle(
        open=base_price + 3, high=base_price + 5,
        low=base_price - 8, close=base_price - 5,
        volume=400,
    ))
    # Last: bullish engulfing
    candles.append(Candle(
        open=base_price - 6, high=base_price + 8,
        low=base_price - 7, close=base_price + 6,
        volume=450,
    ))
    return candles


def _make_bearish_candles(base_price: float, count: int = 10) -> list[Candle]:
    """Generate candles with a bearish reversal at the end (bearish engulfing)."""
    candles = []
    for i in range(count - 2):
        candles.append(Candle(
            open=base_price, high=base_price + 5,
            low=base_price - 5, close=base_price - 2,
            volume=500,
        ))
    # Second-to-last: bullish candle
    candles.append(Candle(
        open=base_price - 3, high=base_price + 8,
        low=base_price - 5, close=base_price + 5,
        volume=400,
    ))
    # Last: bearish engulfing
    candles.append(Candle(
        open=base_price + 6, high=base_price + 7,
        low=base_price - 8, close=base_price - 6,
        volume=450,
    ))
    return candles


class TestVWAPSignalWithIndexLevels:
    """Verify strategy emits index_sl/index_target from market structure."""

    def test_call_signal_has_index_sl_and_target(self):
        # NIFTY at 24050, VWAP at 24030 (0.08% below — between min_distance 0.05% and proximity 0.15%)
        # VWAP bands: lower=24000, upper=24100
        # PDH=24200, PDL=23900
        # CPR: BC=24010, TC=24090, S1=23950, R1=24130
        ctx = MarketContext(
            symbol="NIFTY",
            current_price=24050,
            candles_5m=_make_bullish_candles(24050),
            vwap=VWAPResult(vwap=24030, upper_band=24100, lower_band=24000),
            previous_day=PreviousDayLevels(
                pdh=24200, pdl=23900, pdc=24000, pdo=23950,
                day_range=300, bias=DayBias.BULLISH,
            ),
            cpr=CPRResult(
                pivot=24033, tc=24090, bc=24010,
                r1=24130, s1=23950, r2=24250, s2=23850,
                cpr_type=CPRType.NARROW, cpr_width_pct=0.05,
            ),
            oi_analysis=OIAnalysis(
                pcr=1.1, max_ce_oi_strike=24200, max_pe_oi_strike=23900,
                total_ce_oi=500000, total_pe_oi=550000,
                max_pain=24000, sentiment="NEUTRAL",
            ),
            india_vix=13.5,
            current_time_ist="2026-04-17T10:30:00+05:30",
        )

        strategy = VWAPPullbackStrategy()
        signal = strategy.evaluate(ctx)

        assert signal is not None
        assert signal.signal_type == SignalType.BUY_CE
        assert signal.instrument_type == InstrumentType.OPTION

        # Index-level SL/target should be set from market structure
        assert signal.index_sl is not None, "index_sl should be set"
        assert signal.index_target is not None, "index_target should be set"

        # SL should be below entry (support level)
        assert signal.index_sl < 24050
        # Target should be above entry (resistance level)
        assert signal.index_target > 24050

        # Verify they're in indicators dict too
        assert "index_sl" in signal.indicators
        assert "index_target" in signal.indicators
        assert signal.indicators["index_sl"] == signal.index_sl
        assert signal.indicators["index_target"] == signal.index_target

        # Should NOT have fixed sl_pct/rr_multiplier (market structure was available)
        assert "sl_pct" not in signal.indicators
        assert "rr_multiplier" not in signal.indicators

        print(f"\n--- CALL Signal ---")
        print(f"Entry (index): {signal.entry_price}")
        print(f"Index SL:      {signal.index_sl}")
        print(f"Index Target:  {signal.index_target}")
        rr = (signal.index_target - signal.entry_price) / (signal.entry_price - signal.index_sl)
        print(f"R:R ratio:     1:{rr:.2f}")

    def test_put_signal_has_index_sl_and_target(self):
        # NIFTY at 24050, VWAP at 24070 (0.08% above — between min_distance 0.05% and proximity 0.15%)
        ctx = MarketContext(
            symbol="NIFTY",
            current_price=24050,
            candles_5m=_make_bearish_candles(24050),
            vwap=VWAPResult(vwap=24070, upper_band=24100, lower_band=24000),
            previous_day=PreviousDayLevels(
                pdh=24200, pdl=23900, pdc=24000, pdo=24050,
                day_range=300, bias=DayBias.BEARISH,
            ),
            cpr=CPRResult(
                pivot=24033, tc=24090, bc=24010,
                r1=24130, s1=23950, r2=24250, s2=23850,
                cpr_type=CPRType.NARROW, cpr_width_pct=0.05,
            ),
            oi_analysis=OIAnalysis(
                pcr=0.6, max_ce_oi_strike=24200, max_pe_oi_strike=23900,
                total_ce_oi=600000, total_pe_oi=360000,
                max_pain=24000, sentiment="BEARISH",
            ),
            india_vix=13.5,
            current_time_ist="2026-04-17T10:30:00+05:30",
        )

        strategy = VWAPPullbackStrategy()
        signal = strategy.evaluate(ctx)

        assert signal is not None
        assert signal.signal_type == SignalType.BUY_PE

        assert signal.index_sl is not None
        assert signal.index_target is not None

        # For PE: SL above entry (resistance), target below entry (support)
        assert signal.index_sl > 24050
        assert signal.index_target < 24050

        print(f"\n--- PUT Signal ---")
        print(f"Entry (index): {signal.entry_price}")
        print(f"Index SL:      {signal.index_sl}")
        print(f"Index Target:  {signal.index_target}")
        rr = (signal.entry_price - signal.index_target) / (signal.index_sl - signal.entry_price)
        print(f"R:R ratio:     1:{rr:.2f}")


class TestOptionResolverDeltaConversion:
    """Verify option_resolver converts index-level prices to premium via delta."""

    @pytest.mark.asyncio
    async def test_delta_based_premium_sl_target_for_ce(self):
        from app.services.option_resolver import _compute_premium_sl_target

        # NIFTY at 24050, premium=320, index_sl=24000 (50pt below), index_target=24100 (50pt above)
        sl, target = _compute_premium_sl_target(
            premium=320.0,
            option_type="CE",
            sl_pct=0.30,
            rr_multiplier=1.5,
            label="ATM",
            index_price=24050,
            index_sl=24000,
            index_target=24100,
        )

        # ATM delta=0.50
        # SL = 320 - 0.50 * (24050-24000) = 320 - 25 = 295
        # Target = 320 + 0.50 * (24100-24050) = 320 + 25 = 345
        assert sl == pytest.approx(295.0)
        assert target == pytest.approx(345.0)

        print(f"\n--- CE Delta Conversion ---")
        print(f"Premium: 320, Index: 24050, SL index: 24000, Target index: 24100")
        print(f"Premium SL: {sl}, Premium Target: {target}")

    @pytest.mark.asyncio
    async def test_delta_based_premium_sl_target_for_pe(self):
        from app.services.option_resolver import _compute_premium_sl_target

        # NIFTY at 24050, PE premium=280, index_sl=24100 (50pt above), index_target=24000 (50pt below)
        sl, target = _compute_premium_sl_target(
            premium=280.0,
            option_type="PE",
            sl_pct=0.30,
            rr_multiplier=1.5,
            label="ATM",
            index_price=24050,
            index_sl=24100,
            index_target=24000,
        )

        # ATM delta=0.50
        # PE SL = 280 - 0.50 * (24100-24050) = 280 - 25 = 255
        # PE Target = 280 + 0.50 * (24050-24000) = 280 + 25 = 305
        assert sl == pytest.approx(255.0)
        assert target == pytest.approx(305.0)

        print(f"\n--- PE Delta Conversion ---")
        print(f"Premium: 280, Index: 24050, SL index: 24100, Target index: 24000")
        print(f"Premium SL: {sl}, Premium Target: {target}")

    @pytest.mark.asyncio
    async def test_itm_uses_higher_delta(self):
        from app.services.option_resolver import _compute_premium_sl_target

        sl, target = _compute_premium_sl_target(
            premium=380.0,
            option_type="CE",
            sl_pct=0.30,
            rr_multiplier=1.5,
            label="ITM",
            index_price=24050,
            index_sl=24000,
            index_target=24100,
        )

        # ITM delta=0.60
        # SL = 380 - 0.60 * 50 = 380 - 30 = 350
        # Target = 380 + 0.60 * 50 = 380 + 30 = 410
        assert sl == pytest.approx(350.0)
        assert target == pytest.approx(410.0)

    @pytest.mark.asyncio
    async def test_falls_back_to_pct_when_no_index_levels(self):
        from app.services.option_resolver import _compute_premium_sl_target

        sl, target = _compute_premium_sl_target(
            premium=300.0,
            option_type="CE",
            sl_pct=0.30,
            rr_multiplier=1.5,
            label="ATM",
        )

        # No index levels → fixed pct
        # SL = 300 * (1-0.30) = 210
        # Target = 300 * (1 + 0.30*1.5) = 300 * 1.45 = 435
        assert sl == pytest.approx(210.0)
        assert target == pytest.approx(435.0)

    @pytest.mark.asyncio
    async def test_falls_back_when_delta_produces_invalid_sl(self):
        from app.services.option_resolver import _compute_premium_sl_target

        # index_sl very far from entry → delta math gives negative premium SL
        sl, target = _compute_premium_sl_target(
            premium=200.0,
            option_type="CE",
            sl_pct=0.30,
            rr_multiplier=1.5,
            label="ATM",
            index_price=24050,
            index_sl=23500,  # 550 points away → 0.50*550=275 > premium(200)
            index_target=24200,
        )

        # Delta SL would be 200 - 275 = -75 (invalid) → falls back to pct
        assert sl == pytest.approx(140.0)  # 200 * 0.70
        assert target == pytest.approx(290.0)  # 200 * 1.45
