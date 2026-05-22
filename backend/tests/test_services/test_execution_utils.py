"""Tests for recompute_sl_target in execution_utils.py.

Verifies that all three execution paths (YOLO, Shadow, Manual) will receive
correct SL/target values when the live entry differs from the signal entry.
"""

import pytest
from app.services.execution_utils import recompute_sl_target


# ---------------------------------------------------------------------------
# OPTION tests — proportional percentage scaling
# ---------------------------------------------------------------------------


def test_option_live_entry_higher_scales_up():
    """Premium at execution is higher than signal: SL and target both shift up."""
    sl, tgt = recompute_sl_target(
        signal_entry=100.0,
        signal_sl=70.0,    # 30% below entry
        signal_target=145.0,  # 45% above entry
        live_entry=110.0,
        instrument_type="OPTION",
        signal_type="BUY_CE",
    )
    assert sl == pytest.approx(110.0 * 0.70, abs=0.01)   # 77.0
    assert tgt == pytest.approx(110.0 * 1.45, abs=0.01)  # 159.5


def test_option_live_entry_lower_scales_down():
    """Premium at execution is lower than signal: SL and target both shift down."""
    sl, tgt = recompute_sl_target(
        signal_entry=100.0,
        signal_sl=70.0,
        signal_target=145.0,
        live_entry=90.0,
        instrument_type="OPTION",
        signal_type="BUY_PE",
    )
    assert sl == pytest.approx(90.0 * 0.70, abs=0.01)   # 63.0
    assert tgt == pytest.approx(90.0 * 1.45, abs=0.01)  # 130.5


def test_option_live_entry_same_as_signal_no_change():
    """When live entry equals signal entry, values are unchanged."""
    sl, tgt = recompute_sl_target(
        signal_entry=100.0,
        signal_sl=70.0,
        signal_target=145.0,
        live_entry=100.0,
        instrument_type="OPTION",
        signal_type="BUY_CE",
    )
    assert sl == pytest.approx(70.0, abs=0.01)
    assert tgt == pytest.approx(145.0, abs=0.01)


def test_option_no_target_returns_none():
    """When signal has no target, new target is None; SL is still recomputed."""
    sl, tgt = recompute_sl_target(
        signal_entry=100.0,
        signal_sl=70.0,
        signal_target=None,
        live_entry=110.0,
        instrument_type="OPTION",
        signal_type="BUY_CE",
    )
    assert sl == pytest.approx(77.0, abs=0.01)
    assert tgt is None


def test_option_rr_ratio_preserved():
    """R:R multiplier must be identical before and after recompute."""
    signal_entry, signal_sl, signal_target = 200.0, 140.0, 290.0
    orig_risk = signal_entry - signal_sl      # 60
    orig_reward = signal_target - signal_entry  # 90
    orig_rr = orig_reward / orig_risk           # 1.5

    live_entry = 180.0
    sl, tgt = recompute_sl_target(
        signal_entry, signal_sl, signal_target, live_entry, "OPTION", "BUY_CE"
    )
    new_risk = live_entry - sl
    new_reward = tgt - live_entry
    new_rr = new_reward / new_risk
    assert new_rr == pytest.approx(orig_rr, abs=0.001)


# ---------------------------------------------------------------------------
# FUTURE tests — structural SL unchanged, target recomputed
# ---------------------------------------------------------------------------


def test_future_long_sl_stays_at_level():
    """For BUY_FUT: SL must remain at the structural price level."""
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=480.0,   # ORB low
        signal_target=530.0,
        live_entry=505.0,
        instrument_type="FUTURE",
        signal_type="BUY_FUT",
    )
    assert sl == pytest.approx(480.0, abs=0.01)


def test_future_long_target_recomputed_from_live_entry():
    """For BUY_FUT: target is recomputed from live_entry using original R:R."""
    # orig: entry=500, sl=480, target=530 → risk=20, reward=30, rr=1.5
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=480.0,
        signal_target=530.0,
        live_entry=505.0,
        instrument_type="FUTURE",
        signal_type="BUY_FUT",
    )
    # new_risk = 505 - 480 = 25; new_target = 505 + 1.5 * 25 = 542.5
    assert tgt == pytest.approx(542.5, abs=0.01)


def test_future_short_sl_stays_at_level():
    """For SELL_FUT: SL must remain at the structural price level above entry."""
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=520.0,   # structural resistance
        signal_target=470.0,
        live_entry=495.0,
        instrument_type="FUTURE",
        signal_type="SELL_FUT",
    )
    assert sl == pytest.approx(520.0, abs=0.01)


def test_future_short_target_recomputed_from_live_entry():
    """For SELL_FUT: target is recomputed from live_entry using original R:R."""
    # orig: entry=500, sl=520, target=470 → risk=20, reward=30, rr=1.5
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=520.0,
        signal_target=470.0,
        live_entry=495.0,
        instrument_type="FUTURE",
        signal_type="SELL_FUT",
    )
    # new_risk = 520 - 495 = 25; new_target = 495 - 1.5 * 25 = 457.5
    assert tgt == pytest.approx(457.5, abs=0.01)


def test_future_no_target_returns_none():
    """Futures signal with no target: SL unchanged, target stays None."""
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=480.0,
        signal_target=None,
        live_entry=505.0,
        instrument_type="FUTURE",
        signal_type="BUY_FUT",
    )
    assert sl == pytest.approx(480.0, abs=0.01)
    assert tgt is None


# ---------------------------------------------------------------------------
# Edge-case / fallback tests
# ---------------------------------------------------------------------------


def test_degenerate_entry_equals_sl_returns_originals():
    """When signal entry == SL (zero risk), return originals to avoid div-by-zero."""
    sl, tgt = recompute_sl_target(
        signal_entry=100.0,
        signal_sl=100.0,
        signal_target=130.0,
        live_entry=110.0,
        instrument_type="OPTION",
        signal_type="BUY_CE",
    )
    assert sl == pytest.approx(100.0)
    assert tgt == pytest.approx(130.0)


def test_future_live_entry_past_sl_returns_originals():
    """If live price has already crossed the structural SL, return originals."""
    sl, tgt = recompute_sl_target(
        signal_entry=500.0,
        signal_sl=480.0,
        signal_target=530.0,
        live_entry=475.0,   # below SL for a long
        instrument_type="FUTURE",
        signal_type="BUY_FUT",
    )
    assert sl == pytest.approx(480.0)
    assert tgt == pytest.approx(530.0)


def test_zero_entry_returns_originals():
    """Zero signal_entry is degenerate — return originals unchanged."""
    sl, tgt = recompute_sl_target(
        signal_entry=0.0,
        signal_sl=70.0,
        signal_target=145.0,
        live_entry=110.0,
        instrument_type="OPTION",
        signal_type="BUY_CE",
    )
    assert sl == pytest.approx(70.0)
    assert tgt == pytest.approx(145.0)
