"""Tests for OI snapshot fetch task and candle backfill symbol resolution."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date
from decimal import Decimal


# ---------------------------------------------------------------------------
# Tests for _resolve_fyers_symbol
# ---------------------------------------------------------------------------


class TestResolveFyersSymbol:
    """Tests for the symbol resolution helper in candle_backfill."""

    def test_resolves_index_from_map(self):
        """Index symbols should use FYERS_SYMBOL_MAP."""
        from app.services.candle_backfill import _resolve_fyers_symbol

        assert _resolve_fyers_symbol("NIFTY") == "NSE:NIFTY50-INDEX"
        assert _resolve_fyers_symbol("BANKNIFTY") == "NSE:NIFTYBANK-INDEX"
        assert _resolve_fyers_symbol("SENSEX") == "BSE:SENSEX-INDEX"

    def test_resolves_stock_to_nse_eq(self):
        """Stock symbols not in FYERS_SYMBOL_MAP default to NSE:{SYM}-EQ."""
        from app.services.candle_backfill import _resolve_fyers_symbol

        assert _resolve_fyers_symbol("RELIANCE") == "NSE:RELIANCE-EQ"
        assert _resolve_fyers_symbol("TCS") == "NSE:TCS-EQ"
        assert _resolve_fyers_symbol("HDFCBANK") == "NSE:HDFCBANK-EQ"

    def test_india_vix_resolves_from_map(self):
        """India VIX is in the map but excluded from backfill separately."""
        from app.services.candle_backfill import _resolve_fyers_symbol

        assert _resolve_fyers_symbol("INDIA VIX") == "NSE:INDIAVIX-INDEX"


# ---------------------------------------------------------------------------
# Tests for _get_all_backfill_symbols
# ---------------------------------------------------------------------------


class TestGetAllBackfillSymbols:
    """Tests for the combined symbol list builder."""

    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    async def test_includes_default_indices_minus_vix(self, mock_session_factory):
        """Should include all FYERS_SYMBOL_MAP entries except INDIA VIX."""
        from app.services.candle_backfill import _get_all_backfill_symbols

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        symbols = await _get_all_backfill_symbols()

        assert "NIFTY" in symbols
        assert "BANKNIFTY" in symbols
        assert "SENSEX" in symbols
        assert "INDIA VIX" not in symbols

    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    async def test_includes_strategy_configured_symbols(self, mock_session_factory):
        """Strategy symbols should be merged into the backfill list."""
        from app.services.candle_backfill import _get_all_backfill_symbols

        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Query now returns (symbols, symbol_map) tuples
        mock_result.all.return_value = [
            (
                ["NIFTY", "RELIANCE", "TCS"],
                {"RELIANCE": "NSE:RELIANCE-EQ", "TCS": "NSE:TCS-EQ"},
            ),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        symbols = await _get_all_backfill_symbols()

        # Indices from map
        assert "NIFTY" in symbols
        # Strategy additions — uses stored symbol_map
        assert "RELIANCE" in symbols
        assert symbols["RELIANCE"] == "NSE:RELIANCE-EQ"
        assert "TCS" in symbols
        assert symbols["TCS"] == "NSE:TCS-EQ"

    @pytest.mark.asyncio
    @patch("app.services.candle_backfill.async_session_factory")
    async def test_deduplicates_symbols(self, mock_session_factory):
        """NIFTY in both map and strategy config should not duplicate."""
        from app.services.candle_backfill import _get_all_backfill_symbols

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (["NIFTY", "BANKNIFTY"], {}),  # Already in FYERS_SYMBOL_MAP
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        symbols = await _get_all_backfill_symbols()

        # Count NIFTY occurrences — should be exactly 1
        nifty_count = list(symbols.keys()).count("NIFTY")
        assert nifty_count == 1


# ---------------------------------------------------------------------------
# Tests for OI snapshot parsing
# ---------------------------------------------------------------------------


class TestOISnapshotParsing:
    """Tests for _parse_and_store OI data parsing."""

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    async def test_parses_valid_option_chain(self, mock_session_factory):
        """Valid Fyers option chain data should be parsed into OI rows."""
        from app.tasks.oi_snapshot_task import _parse_and_store

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        data = {
            "s": "ok",
            "data": {
                "expiryData": [{"date": "2026-04-22"}],
                "optionsChain": [
                    {
                        "strike_price": 24000,
                        "call_options": {"oi": 500000, "chng_oi": 10000, "volume": 50000},
                        "put_options": {"oi": 600000, "chng_oi": -5000, "volume": 40000},
                    },
                    {
                        "strike_price": 24050,
                        "call_options": {"oi": 300000, "chng_oi": 8000, "volume": 30000},
                        "put_options": {"oi": 400000, "chng_oi": -2000, "volume": 25000},
                    },
                ],
            },
        }

        count = await _parse_and_store("NIFTY", data)

        # 2 strikes × 2 option types = 4 rows
        assert count == 4
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_chain(self):
        """Empty option chain should return 0."""
        from app.tasks.oi_snapshot_task import _parse_and_store

        data = {
            "s": "ok",
            "data": {
                "expiryData": [{"date": "2026-04-22"}],
                "optionsChain": [],
            },
        }

        count = await _parse_and_store("NIFTY", data)
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_expiry(self):
        """Missing expiry data should return 0."""
        from app.tasks.oi_snapshot_task import _parse_and_store

        data = {
            "s": "ok",
            "data": {
                "expiryData": [],
                "optionsChain": [
                    {"strike_price": 24000, "call_options": {"oi": 100}, "put_options": {"oi": 200}},
                ],
            },
        }

        count = await _parse_and_store("NIFTY", data)
        assert count == 0

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    async def test_skips_zero_oi_strikes(self, mock_session_factory):
        """Strikes with 0 OI should not be persisted."""
        from app.tasks.oi_snapshot_task import _parse_and_store

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        data = {
            "s": "ok",
            "data": {
                "expiryData": [{"date": "2026-04-22"}],
                "optionsChain": [
                    {
                        "strike_price": 24000,
                        "call_options": {"oi": 500000, "chng_oi": 10000, "volume": 50000},
                        "put_options": {"oi": 0, "chng_oi": 0, "volume": 0},
                    },
                ],
            },
        }

        count = await _parse_and_store("NIFTY", data)

        # Only CE row (PE has 0 OI)
        assert count == 1


class TestOIFetchGuards:
    """Tests for the OI fetch guard conditions."""

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=False)
    async def test_skips_when_market_closed(self, mock_market):
        """OI fetch should skip when market is closed."""
        from app.tasks.oi_snapshot_task import fetch_oi_snapshots

        # Should return without doing anything
        await fetch_oi_snapshots()
        # No error = success (it just returned early)
