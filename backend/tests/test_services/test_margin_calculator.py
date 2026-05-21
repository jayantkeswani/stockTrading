"""Tests for margin_calculator — heuristic margin estimation for Indian F&O trades."""

import pytest

from app.services.margin_calculator import compute_margin


class TestOptionMargin:

    def test_option_margin_is_premium_paid(self):
        # Options: margin = entry_price * quantity (no leverage, full premium upfront)
        result = compute_margin("NIFTY", 200.0, 75, "OPTION")
        assert result == 200.0 * 75  # 15000.0

    def test_option_margin_ignores_symbol_tier(self):
        # For options the symbol-tier lookup is bypassed entirely
        # RELIANCE has tier 0.18 for futures, but option margin must still be full premium
        result = compute_margin("RELIANCE", 150.0, 250, "OPTION")
        assert result == 150.0 * 250  # 37500.0

    def test_option_margin_zero_price(self):
        # Premium of zero (expired worthless) → margin still 0
        result = compute_margin("BANKNIFTY", 0.0, 30, "OPTION")
        assert result == 0.0


class TestFuturesMarginKnownSymbol:

    def test_futures_margin_reliance(self):
        # RELIANCE tier = 0.18
        entry_price = 1400.0
        quantity = 250
        result = compute_margin("RELIANCE", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.18)

    def test_futures_margin_hdfcbank(self):
        # HDFCBANK tier = 0.16
        entry_price = 1600.0
        quantity = 550
        result = compute_margin("HDFCBANK", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.16)

    def test_futures_margin_tcs(self):
        # TCS is in the "large-cap volatile" group at 0.27
        entry_price = 3500.0
        quantity = 150
        result = compute_margin("TCS", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.27)

    def test_futures_margin_vedl(self):
        # VEDL is a mid-cap F&O stock at 0.28
        entry_price = 400.0
        quantity = 1000
        result = compute_margin("VEDL", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.28)

    def test_futures_margin_idea(self):
        # IDEA is highest-tier mid-cap at 0.35
        entry_price = 10.0
        quantity = 5000
        result = compute_margin("IDEA", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.35)


class TestFuturesMarginIndexSymbol:

    def test_futures_margin_nifty(self):
        # NIFTY index futures: MARGIN_TIER_MAP["NIFTY"] = 1.0 (full premium — same as options)
        entry_price = 24000.0
        quantity = 75
        result = compute_margin("NIFTY", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 1.0)

    def test_futures_margin_banknifty(self):
        # BANKNIFTY index futures: MARGIN_TIER_MAP["BANKNIFTY"] = 1.0
        entry_price = 52000.0
        quantity = 30
        result = compute_margin("BANKNIFTY", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 1.0)


class TestFuturesMarginUnknownSymbol:

    def test_futures_margin_unknown_symbol_uses_default(self):
        # Symbol not in MARGIN_TIER_MAP → falls back to MARGIN_TIER_DEFAULT = 0.20
        entry_price = 500.0
        quantity = 400
        result = compute_margin("UNKNOWNSYM", entry_price, quantity, "FUTURE")
        assert result == pytest.approx(entry_price * quantity * 0.20)

    def test_futures_margin_new_ipo_symbol_uses_default(self):
        # Newly listed F&O stock not yet in the tier map → 20% default
        result = compute_margin("NEWSTOCK", 800.0, 300, "FUTURE")
        assert result == pytest.approx(800.0 * 300 * 0.20)


class TestZeroQuantity:

    def test_zero_quantity_option(self):
        result = compute_margin("NIFTY", 200.0, 0, "OPTION")
        assert result == 0.0

    def test_zero_quantity_future_known_symbol(self):
        result = compute_margin("RELIANCE", 1400.0, 0, "FUTURE")
        assert result == 0.0

    def test_zero_quantity_future_unknown_symbol(self):
        result = compute_margin("UNKNOWNSYM", 500.0, 0, "FUTURE")
        assert result == 0.0
