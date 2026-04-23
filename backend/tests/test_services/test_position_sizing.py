"""Tests for position_sizing.calculate_lots — parity with the old duplicated helper."""

import pytest

from app.services.position_sizing import calculate_lots


def test_basic_calculation():
    # capital=1_000_000, risk=2%, entry=200, sl=140, lot=75
    # risk_amount = 20_000
    # risk_per_lot = (200-140) * 75 = 4500
    # lots = 20000 / 4500 = 4
    assert calculate_lots(1_000_000, 2.0, 200.0, 140.0, 75) == 4


def test_minimum_one_lot():
    # Very small capital + tiny risk % → risk_amount rounds to 0 → returns 1
    # risk_amount = 1000 * 0.01% = 0.1, risk_per_lot = 1 * 75 = 75 → int(0.1/75)=0 → 1
    assert calculate_lots(1_000, 0.01, 500.0, 499.0, 75) == 1


def test_zero_risk_per_lot_returns_one():
    # entry == stop_loss → risk_per_lot=0 → guard triggers
    assert calculate_lots(1_000_000, 2.0, 200.0, 200.0, 75) == 1


def test_small_capital():
    assert calculate_lots(100_000, 2.0, 100.0, 70.0, 25) >= 1


def test_parity_with_original_formula():
    """Verify output matches the exact formula from the old duplicated implementation."""
    capital, risk_pct, entry, sl, lot = 1_500_000, 1.5, 350.0, 245.0, 30
    risk_amount = capital * (risk_pct / 100.0)
    risk_per_lot = abs(entry - sl) * lot
    expected = max(int(risk_amount / risk_per_lot), 1)
    assert calculate_lots(capital, risk_pct, entry, sl, lot) == expected
