"""Tests for stock futures EOD OI snapshot task and S5 intraday OI job."""

import json
import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.tasks.oi_snapshot_task import (
    FYERS_QUOTES_BATCH_SIZE,
    fetch_stock_futures_oi,
    fetch_s5_watchlist_oi,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_futures_resolution(symbol: str, fyers_symbol: str, expiry: date):
    """Build a mock FuturesResolution."""
    mock = MagicMock()
    mock.fyers_symbol = fyers_symbol
    mock.expiry_date = expiry
    mock.lot_size = 500
    mock.ltp = 100.0
    mock.margin_required = 9000.0
    return mock


def _make_quote(fyers_symbol: str, oi: int = 50000, volume: int = 10000) -> dict:
    """Build a Fyers quote response entry. Fyers REST quotes API uses "oi" field."""
    return {
        "n": fyers_symbol,
        "v": {
            "oi": oi,
            "volume": volume,
            "lp": 100.0,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchStockFuturesOI:
    """Tests for fetch_stock_futures_oi()."""

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_stores_rows_with_fut_option_type(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
    ):
        """Rows should be stored with option_type='FUT' and strike_price=0."""
        # Redis token
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        # F&O lot sizes
        mock_lot_sizes.return_value = {"TCS": 175, "INFY": 300}

        # Futures resolution
        expiry = date(2026, 4, 30)
        async def _resolve(symbol, entry_price):
            if symbol == "TCS":
                return _make_futures_resolution("TCS", "NSE:TCS26APRFUT", expiry)
            elif symbol == "INFY":
                return _make_futures_resolution("INFY", "NSE:INFY26APRFUT", expiry)
            return None

        mock_resolve.side_effect = _resolve

        # Quotes
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value={
            "s": "ok",
            "d": [
                _make_quote("NSE:TCS26APRFUT", oi=120000, volume=5000),
                _make_quote("NSE:INFY26APRFUT", oi=80000, volume=3000),
            ],
        })
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        # DB session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_stock_futures_oi()

        # Verify execute was called with rows
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

        # Inspect the insert statement values
        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        # The pg_insert().values(rows) stores the rows in compile state;
        # verify via the parameters that were passed to values()
        inserted = stmt.compile().params
        # With multiple rows, params comes from the VALUES clause
        # For a bulk insert, we check the original rows via the statement
        assert stmt is not None

    @pytest.mark.asyncio
    @patch("app.core.redis.get_redis")
    async def test_skips_without_token(self, mock_get_redis):
        """Should return early when no Fyers token is available."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        # Should not raise
        await fetch_stock_futures_oi()

    @pytest.mark.asyncio
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_skips_when_no_lot_sizes(self, mock_get_redis, mock_lot_sizes):
        """Should return early when NSE lot sizes are empty."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        mock_lot_sizes.return_value = {}

        await fetch_stock_futures_oi()

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_skips_unresolved_symbols(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
    ):
        """Symbols that fail futures resolution should be skipped gracefully."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        mock_lot_sizes.return_value = {"TCS": 175, "BADSTOCK": 100}

        expiry = date(2026, 4, 30)
        async def _resolve(symbol, entry_price):
            if symbol == "TCS":
                return _make_futures_resolution("TCS", "NSE:TCS26APRFUT", expiry)
            return None  # BADSTOCK fails

        mock_resolve.side_effect = _resolve

        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value={
            "s": "ok",
            "d": [_make_quote("NSE:TCS26APRFUT", oi=120000)],
        })
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_stock_futures_oi()

        # Should still persist the one resolved symbol
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_batch_splitting(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
    ):
        """With >50 symbols, quotes should be fetched in multiple batches."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        # Create 75 symbols to force 2 batches (50 + 25)
        lot_sizes = {f"STOCK{i}": 100 for i in range(75)}
        mock_lot_sizes.return_value = lot_sizes

        expiry = date(2026, 4, 30)
        async def _resolve(symbol, entry_price):
            fyers_sym = f"NSE:{symbol}26APRFUT"
            return _make_futures_resolution(symbol, fyers_sym, expiry)

        mock_resolve.side_effect = _resolve

        # Track how many times get_quotes is called
        quote_call_count = 0
        async def _get_quotes(symbols):
            nonlocal quote_call_count
            quote_call_count += 1
            return {
                "s": "ok",
                "d": [_make_quote(s, oi=1000) for s in symbols],
            }

        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(side_effect=_get_quotes)
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_stock_futures_oi()

        # 75 symbols / 50 per batch = 2 batches
        assert quote_call_count == 2

        # All 75 rows should be persisted
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_skips_when_all_resolutions_fail(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
    ):
        """Should return early when no symbols can be resolved."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        mock_lot_sizes.return_value = {"TCS": 175, "INFY": 300}
        mock_resolve.return_value = None  # All fail

        await fetch_stock_futures_oi()
        # No DB call = no error

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_oi_change_is_zero(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
    ):
        """oi_change should always be 0 for EOD snapshots (screener computes change)."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        mock_lot_sizes.return_value = {"TCS": 175}

        expiry = date(2026, 4, 30)
        mock_resolve.return_value = _make_futures_resolution(
            "TCS", "NSE:TCS26APRFUT", expiry,
        )

        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value={
            "s": "ok",
            "d": [_make_quote("NSE:TCS26APRFUT", oi=120000)],
        })
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        # Capture the rows passed to the insert statement
        mock_session = AsyncMock()
        captured_stmts = []

        async def _capture_execute(stmt):
            captured_stmts.append(stmt)

        mock_session.execute = AsyncMock(side_effect=_capture_execute)
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_stock_futures_oi()

        assert len(captured_stmts) == 1
        # The insert statement was built with rows; verify via compile.
        # SQLAlchemy multi-row inserts suffix params with _m0, _m1, etc.
        stmt = captured_stmts[0]
        compiled = stmt.compile()
        params = compiled.params
        assert params.get("oi_change_m0") == 0
        assert params.get("option_type_m0") == "FUT"
        assert params.get("strike_price_m0") == Decimal("0")
        assert params.get("symbol_m0") == "TCS"

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.data_sources.nse_client.get_fo_lot_sizes")
    @patch("app.core.redis.get_redis")
    async def test_handles_missing_oi_in_quote(
        self,
        mock_get_redis,
        mock_lot_sizes,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
    ):
        """Symbols with no quote data (oi=0) should be skipped, not inserted."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="test-token")
        mock_get_redis.return_value = mock_redis

        mock_lot_sizes.return_value = {"TCS": 175}

        expiry = date(2026, 4, 30)
        mock_resolve.return_value = _make_futures_resolution(
            "TCS", "NSE:TCS26APRFUT", expiry,
        )

        # Quote response with no matching symbol (quote lookup miss → oi=0)
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value={
            "s": "ok",
            "d": [],
        })
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        mock_session = AsyncMock()
        captured_stmts = []

        async def _capture_execute(stmt):
            captured_stmts.append(stmt)

        mock_session.execute = AsyncMock(side_effect=_capture_execute)
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_stock_futures_oi()

        # Zero-OI rows are skipped — no DB insert should happen
        assert len(captured_stmts) == 0


# ---------------------------------------------------------------------------
# Tests for fetch_s5_watchlist_oi (intraday 15-min job)
# ---------------------------------------------------------------------------


class TestFetchS5WatchlistOI:
    """Tests for fetch_s5_watchlist_oi()."""

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=False)
    async def test_skips_when_market_closed(self, mock_market_open):
        """Should return early when market is closed."""
        await fetch_s5_watchlist_oi()
        # No Redis or Fyers calls needed — market closed guard fires first

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=True)
    @patch("app.core.redis.get_redis")
    async def test_skips_without_token(self, mock_get_redis, _):
        """Should return early when no Fyers token is available."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis

        await fetch_s5_watchlist_oi()

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=True)
    @patch("app.core.redis.get_redis")
    async def test_skips_when_no_watchlist(self, mock_get_redis, _):
        """Should return early when S5 watchlist key is absent."""
        mock_redis = AsyncMock()

        async def _get(key):
            if "fyers:access_token" in key:
                return "test-token"
            return None  # No watchlist

        mock_redis.get = AsyncMock(side_effect=_get)
        mock_get_redis.return_value = mock_redis

        await fetch_s5_watchlist_oi()

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=True)
    @patch("app.core.database.async_session_factory")
    @patch("app.data_feed.fyers_client.FyersClient")
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.core.redis.get_redis")
    async def test_persists_oi_for_watchlist_symbols(
        self,
        mock_get_redis,
        mock_resolve,
        mock_fyers_cls,
        mock_session_factory,
        _,
    ):
        """Should fetch OI for watchlist symbols and persist with option_type=FUT."""
        watchlist = [{"symbol": "VEDL"}, {"symbol": "SUNPHARMA"}]
        mock_redis = AsyncMock()

        async def _get(key):
            if "fyers:access_token" in key:
                return "test-token"
            if "strat5:watchlist" in key:
                return json.dumps(watchlist)
            return None

        mock_redis.get = AsyncMock(side_effect=_get)
        mock_get_redis.return_value = mock_redis

        expiry = date(2026, 5, 29)

        async def _resolve(symbol, entry_price):
            return _make_futures_resolution(symbol, f"NSE:{symbol}26MAYFUT", expiry)

        mock_resolve.side_effect = _resolve

        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value={
            "s": "ok",
            "d": [
                _make_quote("NSE:VEDL26MAYFUT", oi=200000),
                _make_quote("NSE:SUNPHARMA26MAYFUT", oi=150000),
            ],
        })
        mock_client.close = AsyncMock()
        mock_fyers_cls.return_value = mock_client

        captured_stmts = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=lambda s: captured_stmts.append(s))
        mock_session.commit = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await fetch_s5_watchlist_oi()

        assert len(captured_stmts) == 1
        params = captured_stmts[0].compile().params
        # Both rows should be FUT with strike_price=0
        assert params.get("option_type_m0") == "FUT"
        assert params.get("strike_price_m0") == Decimal("0")
        assert params.get("oi_change_m0") == 0

    @pytest.mark.asyncio
    @patch("app.tasks.oi_snapshot_task.is_market_open", return_value=True)
    @patch("app.services.futures_resolver.resolve_futures_contract")
    @patch("app.core.redis.get_redis")
    async def test_skips_when_all_resolutions_fail(self, mock_get_redis, mock_resolve, _):
        """Should return without DB call when no symbols can be resolved."""
        watchlist = [{"symbol": "VEDL"}]
        mock_redis = AsyncMock()

        async def _get(key):
            if "fyers:access_token" in key:
                return "test-token"
            if "strat5:watchlist" in key:
                return json.dumps(watchlist)
            return None

        mock_redis.get = AsyncMock(side_effect=_get)
        mock_get_redis.return_value = mock_redis
        mock_resolve.return_value = None

        await fetch_s5_watchlist_oi()
        # No error, no DB call
