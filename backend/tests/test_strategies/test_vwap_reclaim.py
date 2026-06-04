"""Tests for Strategy 7: VWAP Reclaim (index options) — the S2 entry redesign.

Drives the state machine candle-by-candle (mirroring live per-1m evaluation): a 5m
bullish reversal pulls back into the VWAP band (ARM) → a subsequent 1m candle closes
back above the reversal candle's high (RECLAIM) → fires with a tight swing stop.
Covers the happy path, aborts (no-reclaim, slice-through), the STRONG-bias gate, and
the 5m aggregation helper.

VWAP is computed from the candle series each step (futures-volume-weighted in live; here
the candle volumes ARE the volume), so the script pins VWAP ≈ 100 with a flat run, then a
low-volume bullish-pin reversal closes ~0.10% above VWAP (inside the 0.05–0.15% band).
"""

from types import SimpleNamespace

import pytest

from app.core.enums import DayBias, SignalType
from app.indicators.candle_patterns import Candle
from app.indicators.vwap import calculate_vwap
from app.services.strategy_params import get_defaults_for_strategy
from app.strategies.base import MarketContext
from app.strategies.strategy_7_vwap_reclaim import VWAPReclaimStrategy, _completed_5m


def c(o, h, l, close, v=100):
    return Candle(open=o, high=h, low=l, close=close, volume=v)


# 20 flat candles pin VWAP ≈ 100 (4 completed 5m bars). Then a low-volume bullish-pin
# 5m reversal (bars 20-24) dips to 99.6 and closes 100.10 → ~0.10% above VWAP (in band).
_FLAT = [c(100.0, 100.0, 100.0, 100.0, v=100) for _ in range(20)]
_REVERSAL = [
    c(100.00, 100.10, 99.60, 99.70, v=10),
    c(99.70, 99.80, 99.60, 99.70, v=10),
    c(99.70, 99.80, 99.65, 99.75, v=10),
    c(99.75, 100.00, 99.70, 99.95, v=10),
    c(99.95, 100.15, 99.90, 100.10, v=10),   # arm candle: close 100.10, reversal high 100.15
]
_RECLAIM = c(100.10, 100.40, 100.05, 100.30, v=100)   # closes above the 100.15 trigger, green
_SWING = 99.60          # reversal 5m low
_TRIGGER = 100.15       # reversal 5m high


def _params(**overrides):
    p = get_defaults_for_strategy("vwap_reclaim")
    p.update(overrides)
    return p


def _ctx(candles_1m, params, intraday_bias=None):
    """Build a MarketContext with VWAP computed from the candles so far (live parity)."""
    vwap = calculate_vwap(
        [x.high for x in candles_1m], [x.low for x in candles_1m],
        [x.close for x in candles_1m], [x.volume for x in candles_1m],
    )
    prev = SimpleNamespace(pdh=101.0, pdl=99.0, pdc=100.0)
    return MarketContext(
        symbol="NIFTY",
        current_price=candles_1m[-1].close,
        candles_5m=[],
        vwap=vwap,
        previous_day=prev,
        cpr=None,
        oi_analysis=None,
        india_vix=None,
        current_time_ist="10:00",
        intraday_bias=intraday_bias,
        strategy_params=params,
        candles_1m=list(candles_1m),
        candles_5m_futures_volume=None,
    )


def _drive(strategy, candles, params, intraday_bias=None):
    """Append candles one at a time, calling evaluate() each step (per-1m close)."""
    signals = []
    for i in range(1, len(candles) + 1):
        sig = strategy.evaluate(_ctx(candles[:i], params, intraday_bias))
        if sig is not None:
            signals.append((i, sig))
    return signals


# ── 5m aggregation helper ─────────────────────────────────────────────────────

def test_completed_5m_drops_partial_tail_and_sums_volume():
    candles = _FLAT[:7]   # 7 → one completed 5m bar, 2 left over
    bars = _completed_5m(candles)
    assert len(bars) == 1
    assert bars[0].volume == 500          # 5 × 100
    assert bars[0].open == 100.0 and bars[0].close == 100.0


# ── state machine ─────────────────────────────────────────────────────────────

def test_happy_path_fires_on_reclaim_with_tight_swing_stop():
    s = VWAPReclaimStrategy()
    candles = _FLAT + _REVERSAL + [_RECLAIM]
    signals = _drive(s, candles, _params())

    assert len(signals) == 1, f"expected exactly one signal, got {len(signals)}"
    idx, sig = signals[0]
    assert idx == len(candles)                        # fired on the reclaim candle
    assert sig.signal_type == SignalType.BUY_CE
    assert sig.entry_price == pytest.approx(100.30)
    # SL sits a hair below the pullback swing (99.60) — the tight, mechanical stop.
    assert sig.index_sl < _SWING
    assert sig.index_sl == pytest.approx(_SWING - 100.30 * 0.03 / 100, abs=1e-4)
    # Target by R:R (1.5) from the tight stop, above entry.
    assert sig.index_target > sig.entry_price
    risk = sig.entry_price - sig.index_sl
    reward = sig.index_target - sig.entry_price
    assert reward / risk == pytest.approx(1.5, abs=0.01)
    assert sig.indicators["setup_type"] == "VWAP_RECLAIM"
    assert sig.indicators["entry_style"] == "RECLAIM"
    assert sig.indicators["trigger_level"] == pytest.approx(_TRIGGER)


def test_no_fire_without_reclaim():
    """A candle that holds below the reversal high (no reclaim) → no signal."""
    s = VWAPReclaimStrategy()
    no_reclaim = c(100.10, 100.14, 100.05, 100.12, v=100)   # close 100.12 < trigger 100.15
    candles = _FLAT + _REVERSAL + [no_reclaim]
    assert _drive(s, candles, _params()) == []


def test_slice_through_swing_aborts():
    """A 1m close below the pullback swing kills the arm — no later reclaim fires."""
    s = VWAPReclaimStrategy()
    slice_candle = c(99.80, 99.90, 99.40, 99.50, v=100)     # close 99.50 < swing 99.60
    candles = _FLAT + _REVERSAL + [slice_candle, _RECLAIM]
    assert _drive(s, candles, _params()) == []


def test_strong_opposing_bias_blocks_arm():
    """A CE reclaim under a STRONG BEARISH intraday bias is gated out (S2 parity)."""
    s = VWAPReclaimStrategy()
    bias = SimpleNamespace(strength="STRONG", bias=DayBias.BEARISH, score=-0.6, components={})
    candles = _FLAT + _REVERSAL + [_RECLAIM]
    assert _drive(s, candles, _params(), intraday_bias=bias) == []


def test_reclaim_timeout_aborts():
    """No reclaim within reclaim_timeout 1m candles → arm expires before a late reclaim."""
    s = VWAPReclaimStrategy()
    # Hold just below the trigger (above the swing — no slice) past the timeout, then reclaim.
    hold = [c(100.05, 100.13, 100.00, 100.10, v=100) for _ in range(7)]
    candles = _FLAT + _REVERSAL + hold + [_RECLAIM]
    assert _drive(s, candles, _params(reclaim_timeout=5)) == []
