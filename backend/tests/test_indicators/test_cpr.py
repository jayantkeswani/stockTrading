"""Tests for CPR (Central Pivot Range) indicator."""

from app.core.enums import CPRType
from app.indicators.cpr import CPRResult, calculate_cpr


class TestCalculateCPR:

    def test_known_values(self):
        """Hand-calculate CPR from H=110, L=90, C=105.

        pivot = (110+90+105)/3 = 101.6667
        bc = (110+90)/2 = 100.0
        tc = (pivot - bc) + pivot = (101.6667 - 100) + 101.6667 = 103.3333
        r1 = 2*pivot - low = 2*101.6667 - 90 = 113.3333
        s1 = 2*pivot - high = 2*101.6667 - 110 = 93.3333
        r2 = pivot + (high - low) = 101.6667 + 20 = 121.6667
        s2 = pivot - (high - low) = 101.6667 - 20 = 81.6667
        """
        result = calculate_cpr(high=110.0, low=90.0, close=105.0)

        expected_pivot = (110 + 90 + 105) / 3.0
        expected_bc = (110 + 90) / 2.0
        expected_tc = (expected_pivot - expected_bc) + expected_pivot

        assert abs(result.pivot - expected_pivot) < 1e-4
        assert abs(result.tc - expected_tc) < 1e-4
        assert abs(result.bc - expected_bc) < 1e-4
        assert abs(result.r1 - (2 * expected_pivot - 90)) < 1e-4
        assert abs(result.s1 - (2 * expected_pivot - 110)) < 1e-4
        assert abs(result.r2 - (expected_pivot + 20)) < 1e-4
        assert abs(result.s2 - (expected_pivot - 20)) < 1e-4

    def test_tc_always_gte_bc(self):
        """TC >= BC after the swap logic."""
        result = calculate_cpr(high=100.0, low=90.0, close=92.0)
        assert result.tc >= result.bc

    def test_tc_bc_swap_when_tc_less_than_bc(self):
        """When close is below midpoint of H+L, raw TC < raw BC, so swap occurs.

        H=100, L=90, C=92
        pivot = (100+90+92)/3 = 94.0
        bc = (100+90)/2 = 95.0
        tc = (94 - 95) + 94 = 93.0  (raw tc < bc => swap)
        After swap: tc=95, bc=93
        """
        result = calculate_cpr(high=100.0, low=90.0, close=92.0)
        assert abs(result.tc - 95.0) < 1e-9
        assert abs(result.bc - 93.0) < 1e-9

    def test_narrow_cpr_detection(self):
        """CPR width < 0.1% of pivot => NARROW."""
        # We need TC-BC to be very small. When H,L,C are nearly equal:
        # H=100.02, L=99.98, C=100.0
        # pivot = (100.02+99.98+100)/3 = 100.0
        # bc = (100.02+99.98)/2 = 100.0
        # tc = (100 - 100) + 100 = 100.0
        # width = 0, width_pct = 0 < 0.1 => NARROW
        result = calculate_cpr(high=100.02, low=99.98, close=100.0)
        assert result.cpr_type == CPRType.NARROW

    def test_wide_cpr_detection(self):
        """CPR width >= 0.1% of pivot => WIDE."""
        result = calculate_cpr(high=110.0, low=90.0, close=105.0)
        # width_pct will be significant with such a range
        assert result.cpr_type == CPRType.WIDE

    def test_cpr_width_pct_calculation(self):
        """Verify cpr_width_pct = (tc - bc) / pivot * 100."""
        result = calculate_cpr(high=110.0, low=90.0, close=105.0)
        expected_pct = (result.tc - result.bc) / result.pivot * 100
        assert abs(result.cpr_width_pct - expected_pct) < 1e-9

    def test_custom_narrow_threshold(self):
        """With a high threshold, a moderately wide CPR becomes NARROW."""
        result = calculate_cpr(high=110.0, low=90.0, close=105.0, narrow_threshold_pct=50.0)
        assert result.cpr_type == CPRType.NARROW

    def test_symmetric_hlc(self):
        """H=100, L=80, C=90 => pivot=90, bc=90, tc=90 => width=0 => NARROW."""
        result = calculate_cpr(high=100.0, low=80.0, close=90.0)
        # pivot = (100+80+90)/3 = 90, bc = (100+80)/2 = 90, tc = (90-90)+90 = 90
        assert abs(result.pivot - 90.0) < 1e-9
        assert abs(result.tc - 90.0) < 1e-9
        assert abs(result.bc - 90.0) < 1e-9
        assert result.cpr_type == CPRType.NARROW

    def test_returns_cpr_result_type(self):
        result = calculate_cpr(high=100.0, low=90.0, close=95.0)
        assert isinstance(result, CPRResult)

    def test_r1_above_pivot_s1_below_pivot(self):
        result = calculate_cpr(high=110.0, low=90.0, close=100.0)
        assert result.r1 > result.pivot
        assert result.s1 < result.pivot

    def test_r2_above_r1_s2_below_s1(self):
        result = calculate_cpr(high=110.0, low=90.0, close=100.0)
        assert result.r2 > result.r1
        assert result.s2 < result.s1
