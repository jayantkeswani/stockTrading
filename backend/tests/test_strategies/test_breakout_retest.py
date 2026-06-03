"""Tests for Strategy 6: Breakout-Retest Intraday Futures.

Drives the state machine candle-by-candle (mirroring live per-1m evaluation):
break (5m close beyond level) → pullback retest (1m) → reclaim candle fires.
Covers the happy path, aborts (slice-through, timeout), and the hard gates.
"""

from datetime import datetime, time as dt_time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.enums import SignalType
from app.indicators.candle_patterns import Candle
from app.indicators.market_levels import find_pivot_high, find_pivot_low
from app.services.strategy_params import get_defaults_for_strategy
from app.strategies.strategy_6_breakout_retest import BreakoutRetestStrategy

# ── helpers ────────────────────────────────────────────────────────────────

_PDH = 100.0
_NOON = datetime(2026, 6, 4, 10, 0, tzinfo=None)   # 10:00 — inside the arm window


def c(o, h, l, close, v=100):
    return Candle(open=o, high=h, low=l, close=close, volume=v)


def _params(**overrides):
    p = get_defaults_for_strategy("breakout_retest")
    p["enabled_levels"] = ["PDH_PDL"]      # isolate PDH/PDL arming
    p["_nifty_day_change_pct"] = 0.30      # with-trend for a long
    p.update(overrides)
    return p


def _ctx(candles_1m, params, intraday_bias=None):
    from app.strategies.base import MarketContext

    prev = SimpleNamespace(pdh=_PDH, pdl=90.0, pdc=95.0)
    return MarketContext(
        symbol="TCS",
        current_price=candles_1m[-1].close if candles_1m else 100.0,
        candles_5m=[],
        vwap=None,
        previous_day=prev,
        cpr=None,
        oi_analysis=None,
        india_vix=None,
        current_time_ist="10:00",
        candles_daily=None,            # → ADR filter skipped
        intraday_bias=intraday_bias,
        atr_5m=None,
        strategy_params=params,
        candles_1m=list(candles_1m),
    )


# Candle script: 0-4 below PDH, 5-9 breakout (bar1 closes 101), 10-12 pullback
# to ~99.85 (above the 99.8 slice line), 13 = bullish reclaim on 3x volume.
_BREAKOUT = [
    c(99.0, 99.5, 98.5, 99.0), c(99.0, 99.4, 98.6, 99.1), c(99.1, 99.5, 98.7, 99.0),
    c(99.0, 99.5, 98.8, 99.2), c(99.2, 99.6, 98.9, 99.1),                       # bar0 close 99.1
    c(99.1, 99.6, 98.9, 99.3), c(99.3, 100.0, 99.1, 99.8), c(99.8, 100.5, 99.6, 100.3),
    c(100.3, 101.0, 100.1, 100.6), c(100.6, 101.2, 100.4, 101.0),               # bar1 close 101.0 → ARM
]
_PULLBACK = [
    c(101.0, 101.0, 100.10, 100.2),   # 10: low 100.10 ≤ retest proximity → retest_seen
    c(100.2, 100.30, 99.90, 100.0),   # 11: extend pullback swing
    c(100.0, 100.10, 99.85, 99.9),    # 12: swing 99.85 (close 99.9 > 99.8 slice → no abort)
]
_RECLAIM = c(99.9, 100.60, 99.85, 100.4, v=300)   # 13: bullish reclaim, 3x volume


def _drive(strategy, candles, params, intraday_bias=None):
    """Append candles one at a time, calling evaluate() each step (per-1m close)."""
    signals = []
    with patch("app.core.utils.now_ist", return_value=_NOON):
        for i in range(1, len(candles) + 1):
            sig = strategy.evaluate(_ctx(candles[:i], params, intraday_bias))
            if sig is not None:
                signals.append((i, sig))
    return signals


# ── pivot helpers ───────────────────────────────────────────────────────────

def test_find_pivot_high_and_low():
    highs = [c(0, 1, 0, 0), c(0, 2, 0, 0), c(0, 5, 1, 0), c(0, 2, 0, 0), c(0, 1, 0, 0)]
    assert find_pivot_high(highs, left=2, right=2) == 5
    lows = [c(0, 0, 5, 0), c(0, 0, 4, 0), c(0, 0, 1, 0), c(0, 0, 4, 0), c(0, 0, 5, 0)]
    assert find_pivot_low(lows, left=2, right=2) == 1


def test_find_pivot_none_when_too_short():
    assert find_pivot_high([c(0, 1, 0, 0)], left=2, right=2) is None


# ── state machine ────────────────────────────────────────────────────────────

def test_happy_path_fires_on_reclaim_with_tight_sl():
    s = BreakoutRetestStrategy()
    candles = _BREAKOUT + _PULLBACK + [_RECLAIM]
    signals = _drive(s, candles, _params())

    assert len(signals) == 1, f"expected exactly one signal, got {len(signals)}"
    idx, sig = signals[0]
    assert idx == len(candles)                       # fired on the reclaim candle
    assert sig.signal_type == SignalType.BUY_FUT
    assert sig.entry_price == pytest.approx(100.4)
    # SL just below the retest swing (99.85), NOT at the breakout level — tight.
    assert 99.5 < sig.stop_loss < 99.85
    assert sig.stop_loss < _PDH
    # R:R ~1.8 by construction.
    risk = sig.entry_price - sig.stop_loss
    reward = sig.target_price - sig.entry_price
    assert reward / risk == pytest.approx(1.8, abs=0.01)
    assert sig.indicators["setup_type"] == "PDH_PDL_RETEST"
    assert sig.indicators["entry_style"] == "RETEST"


def test_no_fire_without_reclaim():
    """Pullback that never reclaims (drifts below and sits) → no signal."""
    s = BreakoutRetestStrategy()
    candles = _BREAKOUT + _PULLBACK   # stop before the reclaim candle
    assert _drive(s, candles, _params()) == []


def test_slice_through_aborts():
    """A 1m close decisively below the level kills the arm — no later fire."""
    s = BreakoutRetestStrategy()
    slice_candle = c(99.9, 100.0, 99.5, 99.6)        # close 99.6 < 99.8 slice line
    candles = _BREAKOUT + _PULLBACK + [slice_candle, _RECLAIM]
    assert _drive(s, candles, _params()) == []


def test_timeout_aborts():
    """No retest within max_wait_minutes → arm expires, no fire even on a late reclaim."""
    s = BreakoutRetestStrategy()
    # Hold price above the level (no retest) for > max_wait (30) candles, then reclaim.
    hover = [c(100.6, 101.0, 100.5, 100.8) for _ in range(35)]
    candles = _BREAKOUT + hover + _PULLBACK + [_RECLAIM]
    assert _drive(s, candles, _params(max_wait_minutes=30)) == []


def test_with_nifty_trend_gate_blocks_counter_trend():
    """A long fired against a down NIFTY day (nifty_day_change_pct < 0) is blocked."""
    s = BreakoutRetestStrategy()
    candles = _BREAKOUT + _PULLBACK + [_RECLAIM]
    assert _drive(s, candles, _params(_nifty_day_change_pct=-0.30)) == []


def test_opposing_stock_bias_gate_blocks():
    """A long fired against a bearish stock intraday bias is blocked (any opposing)."""
    s = BreakoutRetestStrategy()
    candles = _BREAKOUT + _PULLBACK + [_RECLAIM]
    bias = SimpleNamespace(score=-0.30, components={})
    assert _drive(s, candles, _params(), intraday_bias=bias) == []


def test_weak_reclaim_volume_blocked():
    """Reclaim candle without volume confirmation is gated out."""
    s = BreakoutRetestStrategy()
    weak = c(99.9, 100.60, 99.85, 100.4, v=100)      # same volume as baseline → ratio ~1.0
    candles = _BREAKOUT + _PULLBACK + [weak]
    assert _drive(s, candles, _params(reclaim_vol_mult=1.5)) == []


def test_no_arm_before_window():
    """Before arm_start the strategy does nothing even on a clean breakout+reclaim."""
    s = BreakoutRetestStrategy()
    candles = _BREAKOUT + _PULLBACK + [_RECLAIM]
    early = datetime(2026, 6, 4, 9, 20, tzinfo=None)   # 09:20 < 09:30 arm_start
    with patch("app.core.utils.now_ist", return_value=early):
        sigs = [s.evaluate(_ctx(candles[:i], _params())) for i in range(1, len(candles) + 1)]
    assert all(x is None for x in sigs)
