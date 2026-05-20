"""Tests for brokerage_calculator — Indian F&O charges (Zerodha rates)."""

from decimal import Decimal

import pytest

from app.services.brokerage_calculator import ChargesBreakdown, compute_charges


class TestOptionsCharges:
    """Options: always BUY to open, SELL to close."""

    def test_nifty_call_winning_trade(self):
        # NIFTY CE: entry ₹200, exit ₹250, 1 lot = 75 qty
        c = compute_charges("OPTION", Decimal("200"), Decimal("250"), 75, "BUY")

        assert c.brokerage == Decimal("40")  # ₹20 × 2

        # STT: sell turnover (250×75=18750) × 0.0625%
        assert c.stt == Decimal("11.72")

        # Exchange txn: total turnover (33750) × 0.0495%
        assert c.exchange_txn == Decimal("16.71")

        # GST: 18% × (40 + 16.71)
        assert c.gst == Decimal("10.21")

        # SEBI: 33750 / 10_000_000 × 10
        assert c.sebi_charges == Decimal("0.03")

        # Stamp: buy turnover (15000) × 0.003%
        assert c.stamp_duty == Decimal("0.45")

        # Total = sum of all
        expected_total = c.brokerage + c.stt + c.exchange_txn + c.gst + c.sebi_charges + c.stamp_duty
        assert c.total == expected_total

    def test_options_losing_trade(self):
        # Exit < entry — charges still positive
        c = compute_charges("OPTION", Decimal("200"), Decimal("150"), 75, "BUY")

        assert c.brokerage == Decimal("40")
        # STT on lower exit turnover: 150×75=11250 × 0.0625%
        assert c.stt == Decimal("7.03")
        assert c.total > 0

    def test_options_zero_exit(self):
        # Option expires worthless
        c = compute_charges("OPTION", Decimal("200"), Decimal("0"), 75, "BUY")

        assert c.stt == Decimal("0.00")  # sell turnover is 0
        assert c.stamp_duty == Decimal("0.45")  # buy turnover unchanged
        assert c.total > 0  # brokerage + GST still apply


class TestFuturesCharges:
    """Futures: side determines buy/sell leg."""

    def test_futures_long_trade(self):
        # RELIANCE FUT: BUY ₹1400, exit ₹1450, qty 250
        c = compute_charges("FUTURE", Decimal("1400"), Decimal("1450"), 250, "BUY")

        assert c.brokerage == Decimal("40")

        # STT: sell turnover (1450×250=362500) × 0.0125%
        assert c.stt == Decimal("45.31")

        # Exchange txn: total turnover (712500) × 0.002%
        assert c.exchange_txn == Decimal("14.25")

        # Stamp: buy turnover (1400×250=350000) × 0.002%
        assert c.stamp_duty == Decimal("7.00")

        expected_total = c.brokerage + c.stt + c.exchange_txn + c.gst + c.sebi_charges + c.stamp_duty
        assert c.total == expected_total

    def test_futures_short_trade(self):
        # SELL to open, BUY to close — STT on entry (sell), stamp on exit (buy)
        c = compute_charges("FUTURE", Decimal("1400"), Decimal("1350"), 250, "SELL")

        # Entry is SELL side: sell_turnover = 1400×250 = 350000
        assert c.stt == Decimal("43.75")  # 350000 × 0.0125%

        # Exit is BUY side: buy_turnover = 1350×250 = 337500
        assert c.stamp_duty == Decimal("6.75")  # 337500 × 0.002%

    def test_futures_short_vs_long_stt_differs(self):
        # Same prices, different side → STT applied to different leg
        long = compute_charges("FUTURE", Decimal("1000"), Decimal("1050"), 100, "BUY")
        short = compute_charges("FUTURE", Decimal("1000"), Decimal("1050"), 100, "SELL")

        # LONG: STT on exit (1050×100), SHORT: STT on entry (1000×100)
        assert long.stt != short.stt
        # LONG: stamp on entry (1000×100), SHORT: stamp on exit (1050×100)
        assert long.stamp_duty != short.stamp_duty


class TestPrecisionAndSerialization:

    def test_all_components_are_decimal(self):
        c = compute_charges("OPTION", Decimal("300"), Decimal("350"), 75, "BUY")
        for field in ["brokerage", "stt", "exchange_txn", "gst", "sebi_charges", "stamp_duty", "total"]:
            assert isinstance(getattr(c, field), Decimal), f"{field} should be Decimal"

    def test_total_equals_component_sum(self):
        c = compute_charges("FUTURE", Decimal("500"), Decimal("520"), 500, "BUY")
        component_sum = c.brokerage + c.stt + c.exchange_txn + c.gst + c.sebi_charges + c.stamp_duty
        assert c.total == component_sum

    def test_to_dict_returns_floats(self):
        c = compute_charges("OPTION", Decimal("200"), Decimal("250"), 75, "BUY")
        d = c.to_dict()
        assert isinstance(d, dict)
        for key in ["brokerage", "stt", "exchange_txn", "gst", "sebi_charges", "stamp_duty", "total"]:
            assert key in d
            assert isinstance(d[key], float), f"{key} should be float in to_dict()"

    def test_to_dict_total_matches(self):
        c = compute_charges("FUTURE", Decimal("1400"), Decimal("1450"), 250, "BUY")
        d = c.to_dict()
        assert abs(d["total"] - float(c.total)) < 0.001

    def test_frozen_dataclass(self):
        c = compute_charges("OPTION", Decimal("200"), Decimal("250"), 75, "BUY")
        with pytest.raises(AttributeError):
            c.brokerage = Decimal("100")


class TestEdgeCases:

    def test_string_price_input(self):
        # compute_charges casts to Decimal internally
        c = compute_charges("OPTION", 200, 250, 75, "BUY")
        assert c.total > 0

    def test_small_trade(self):
        # 1 lot of MIDCPNIFTY, small premium
        c = compute_charges("OPTION", Decimal("10"), Decimal("15"), 50, "BUY")
        # Brokerage dominates on tiny trades
        assert c.brokerage == Decimal("40")
        assert c.total > c.brokerage  # other charges add up
