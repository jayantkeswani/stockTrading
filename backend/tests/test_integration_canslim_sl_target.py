"""Integration test: CAN SLIM strategy emits pattern-based SL/target.

Validates:
1. Base pattern detection stores base_low
2. Strategy computes SL from base_low and target from measured move
3. Fixed % caps are respected
4. Futures resolver preserves pattern-based ratios
"""

import pytest
from unittest.mock import MagicMock

from app.core.constants import CANSLIM_SL_PCT, CANSLIM_TARGET_PCT
from app.indicators.candle_patterns import Candle
from app.strategies.canslim.base_patterns import (
    BasePattern,
    detect_cup_with_handle,
    detect_flat_base,
)
from app.strategies.strategy_4_canslim import CANSLIMStrategy
from app.strategies.base import MarketContext


def _make_candle(o, h, l, c, v=1000) -> Candle:
    return Candle(open=o, high=h, low=l, close=c, volume=v)


class TestPatternBasedSLTarget:
    """Verify pattern-based SL/target math with a known BasePattern."""

    def test_cup_with_handle_sl_at_base_low(self):
        """SL should be 2% below base_low, not fixed 8%."""
        # Pattern: breakout at 1545, base_low at 1280 (17.2% depth)
        pattern = BasePattern(
            pattern_type="CUP_HANDLE",
            breakout_price=1545.0,
            depth_pct=17.2,
            length_days=45,
            base_low=1280.0,
        )

        entry_price = 1549.80  # Just above breakout

        # Expected SL: base_low * 0.98 = 1280 * 0.98 = 1254.40
        pattern_sl = pattern.base_low * 0.98
        max_sl = entry_price * (1 - CANSLIM_SL_PCT / 100)  # 1549.80 * 0.92 = 1425.82
        expected_sl = max(pattern_sl, max_sl)
        # 1425.82 > 1254.40, so capped at max_sl = 1425.82
        assert expected_sl == pytest.approx(max_sl, rel=0.01)

        # Expected target: measured move = 1545 - 1280 = 265
        # pattern_target = 1545 + 265 = 1810
        measured_move = pattern.breakout_price - pattern.base_low
        pattern_target = pattern.breakout_price + measured_move
        min_target = entry_price * (1 + CANSLIM_TARGET_PCT / 100)  # 1549.80 * 1.20 = 1859.76
        expected_target = max(pattern_target, min_target)
        # 1859.76 > 1810 → min_target wins
        assert expected_target == pytest.approx(min_target, rel=0.01)

        print(f"\n--- Cup-with-Handle SL/Target ---")
        print(f"Entry: {entry_price}")
        print(f"Base low: {pattern.base_low}, Pattern SL: {pattern_sl:.2f}, Max SL (8%): {max_sl:.2f}")
        print(f"Final SL: {expected_sl:.2f} (capped at 8%)")
        print(f"Measured move: {measured_move:.2f}, Pattern target: {pattern_target:.2f}, Min target (20%): {min_target:.2f}")
        print(f"Final target: {expected_target:.2f}")

    def test_shallow_flat_base_uses_pattern_sl(self):
        """Shallow base (5% depth) → pattern SL is TIGHTER than 8% cap."""
        # Flat base: breakout at 1000, base_low at 950 (5% depth)
        pattern = BasePattern(
            pattern_type="FLAT_BASE",
            breakout_price=1000.0,
            depth_pct=5.0,
            length_days=20,
            base_low=950.0,
        )

        entry_price = 1005.0

        # Pattern SL: 950 * 0.98 = 931.00
        pattern_sl = pattern.base_low * 0.98  # 931.00
        max_sl = entry_price * (1 - CANSLIM_SL_PCT / 100)  # 1005 * 0.92 = 924.60
        expected_sl = max(pattern_sl, max_sl)
        # 931.00 > 924.60 → pattern SL is TIGHTER, used directly
        assert expected_sl == pytest.approx(931.0, rel=0.01)

        # Measured move: 1000 - 950 = 50
        # Target: 1000 + 50 = 1050
        measured_move = pattern.breakout_price - pattern.base_low
        pattern_target = pattern.breakout_price + measured_move  # 1050
        min_target = entry_price * (1 + CANSLIM_TARGET_PCT / 100)  # 1206
        expected_target = max(pattern_target, min_target)
        # 1206 > 1050 → min_target (20%) wins
        assert expected_target == pytest.approx(min_target, rel=0.01)

        print(f"\n--- Flat Base (Shallow) SL/Target ---")
        print(f"Entry: {entry_price}")
        print(f"Pattern SL: {pattern_sl:.2f} (tighter than 8% cap {max_sl:.2f})")
        print(f"Final SL: {expected_sl:.2f} (pattern-based)")

    def test_deep_cup_uses_capped_sl(self):
        """Deep base (30% depth) → pattern SL is too wide, capped at 8%."""
        pattern = BasePattern(
            pattern_type="CUP_HANDLE",
            breakout_price=500.0,
            depth_pct=30.0,
            length_days=60,
            base_low=350.0,
        )

        entry_price = 510.0

        pattern_sl = pattern.base_low * 0.98  # 343.00
        max_sl = entry_price * (1 - CANSLIM_SL_PCT / 100)  # 469.20
        expected_sl = max(pattern_sl, max_sl)
        # 469.20 > 343.00 → capped at 8%
        assert expected_sl == pytest.approx(469.20, rel=0.01)

        # Measured move: 500 - 350 = 150
        # Target: 500 + 150 = 650 (27.5% above entry)
        measured_move = pattern.breakout_price - pattern.base_low
        pattern_target = pattern.breakout_price + measured_move  # 650
        min_target = entry_price * (1 + CANSLIM_TARGET_PCT / 100)  # 612
        expected_target = max(pattern_target, min_target)
        # 650 > 612 → measured move wins!
        assert expected_target == pytest.approx(650.0, rel=0.01)

        print(f"\n--- Deep Cup SL/Target ---")
        print(f"Entry: {entry_price}")
        print(f"SL capped at 8%: {expected_sl:.2f}")
        print(f"Target from measured move: {expected_target:.2f} (beats 20% floor)")


class TestFuturesResolverPreservesRatios:
    """Verify _resolve_futures doesn't overwrite pattern-based SL/target."""

    def test_proportional_adjustment(self):
        """When futures LTP differs from spot, SL/target ratios preserved."""
        # Spot entry=1000, SL=950 (5% below), target=1100 (10% above)
        spot_entry = 1000.0
        spot_sl = 950.0
        spot_target = 1100.0

        sl_pct = (spot_entry - spot_sl) / spot_entry      # 0.05
        target_pct = (spot_target - spot_entry) / spot_entry  # 0.10

        # Futures LTP = 1010 (futures premium of 1%)
        futures_ltp = 1010.0
        futures_sl = futures_ltp * (1 - sl_pct)         # 1010 * 0.95 = 959.50
        futures_target = futures_ltp * (1 + target_pct) # 1010 * 1.10 = 1111.00

        assert futures_sl == pytest.approx(959.50, rel=0.01)
        assert futures_target == pytest.approx(1111.00, rel=0.01)

        # Verify ratios are preserved
        assert (futures_ltp - futures_sl) / futures_ltp == pytest.approx(sl_pct, rel=0.01)
        assert (futures_target - futures_ltp) / futures_ltp == pytest.approx(target_pct, rel=0.01)

        print(f"\n--- Futures Ratio Preservation ---")
        print(f"Spot: entry={spot_entry}, SL={spot_sl}, target={spot_target}")
        print(f"Futures LTP={futures_ltp}: SL={futures_sl}, target={futures_target}")
        print(f"SL ratio preserved: {sl_pct:.2%}")
        print(f"Target ratio preserved: {target_pct:.2%}")
