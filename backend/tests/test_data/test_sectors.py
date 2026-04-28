"""Tests for sector classification lookup."""

from app.data.sectors import get_all_sectors, get_sector, get_sector_stocks


class TestGetSector:
    def test_known_it_stock(self):
        assert get_sector("TCS") == "IT"

    def test_known_banking_stock(self):
        assert get_sector("HDFCBANK") == "BANKING_PRIVATE"

    def test_known_auto_stock(self):
        assert get_sector("TATAMOTORS") == "AUTO"

    def test_unknown_symbol(self):
        assert get_sector("DOESNOTEXIST") is None

    def test_case_sensitive(self):
        assert get_sector("tcs") is None


class TestGetSectorStocks:
    def test_it_sector(self):
        stocks = get_sector_stocks("IT")
        assert "TCS" in stocks
        assert "INFY" in stocks

    def test_unknown_sector(self):
        assert get_sector_stocks("NONEXISTENT") == []


class TestGetAllSectors:
    def test_returns_sectors(self):
        sectors = get_all_sectors()
        assert "IT" in sectors
        assert "BANKING_PRIVATE" in sectors
        assert "AUTO" in sectors
        assert len(sectors) >= 10
