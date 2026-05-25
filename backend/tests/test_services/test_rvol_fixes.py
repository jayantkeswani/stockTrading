"""Tests for RVOL-related fixes:

1. FeedManager first-tick seeding — prevents cumulative volume spike after restart
2. StrategyRunner per-symbol RVOL profiles — prevents cross-symbol contamination
"""

import json

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.data_feed.feed_manager import FeedManager


class TestFirstTickVolumeSeeding:
    """Issue #1: After a mid-session restart, _last_vol_today is empty.
    The first tick carries the full day's cumulative volume (e.g. 2,000,000).
    Without the fix, that entire amount becomes a single candle's volume.
    With the fix, the first tick seeds the baseline and produces zero delta.
    """

    @pytest.mark.asyncio
    async def test_first_tick_seeds_baseline_zero_delta(self):
        """First tick for a symbol after restart should produce zero volume."""
        fm = FeedManager()
        assert "VEDL" not in fm._last_vol_today

        # Simulate mid-session restart: first tick has large cumulative volume
        await fm._aggregate_candle("VEDL", 450.0, 2_000_000)

        # Baseline should be seeded
        assert fm._last_vol_today["VEDL"] == 2_000_000
        # Candle volume should be 0, not 2,000,000
        candle = fm._current_candles["VEDL"]
        assert candle["volume"] == 0

    @pytest.mark.asyncio
    async def test_second_tick_computes_normal_delta(self):
        """After seeding, subsequent ticks should compute correct deltas."""
        fm = FeedManager()

        # First tick seeds
        await fm._aggregate_candle("VEDL", 450.0, 2_000_000)
        assert fm._current_candles["VEDL"]["volume"] == 0

        # Second tick adds 5000 shares of actual trading
        await fm._aggregate_candle("VEDL", 451.0, 2_005_000)
        candle = fm._current_candles["VEDL"]
        assert candle["volume"] == 5_000
        assert candle["close"] == 451.0

    @pytest.mark.asyncio
    async def test_fresh_morning_start_works_correctly(self):
        """At market open (9:15), cumulative volume starts near zero — no spike."""
        fm = FeedManager()

        # Market open: first tick, cumulative volume is tiny
        await fm._aggregate_candle("NIFTY", 25000.0, 100)
        assert fm._current_candles["NIFTY"]["volume"] == 0

        # Second tick
        await fm._aggregate_candle("NIFTY", 25001.0, 350)
        assert fm._current_candles["NIFTY"]["volume"] == 250

    @pytest.mark.asyncio
    async def test_day_boundary_reset_still_works(self):
        """When cumulative volume resets (new day), delta clamps to zero."""
        fm = FeedManager()

        # Simulate end of day — baseline is high
        fm._last_vol_today["NIFTY"] = 5_000_000

        # New day: cumulative resets to near zero
        await fm._aggregate_candle("NIFTY", 25000.0, 200)
        candle = fm._current_candles["NIFTY"]
        # max(0, 200 - 5_000_000) = 0
        assert candle["volume"] == 0

    @pytest.mark.asyncio
    async def test_ws_reconnect_preserves_baseline(self):
        """On WS reconnect (not full restart), _last_vol_today is preserved
        and clear_in_progress_candles doesn't touch it."""
        fm = FeedManager()
        fm._last_vol_today["VEDL"] = 1_500_000
        fm._current_candles["VEDL"] = {"minute_key": "2026-05-20 10:30", "volume": 500}

        fm.clear_in_progress_candles()

        # Baseline preserved
        assert fm._last_vol_today["VEDL"] == 1_500_000
        # In-progress candle cleared
        assert "VEDL" not in fm._current_candles

        # Next tick computes correct delta (not a spike)
        await fm._aggregate_candle("VEDL", 450.0, 1_503_000)
        assert fm._current_candles["VEDL"]["volume"] == 3_000

    @pytest.mark.asyncio
    async def test_multiple_symbols_each_seed_independently(self):
        """Each symbol seeds its own baseline on first tick."""
        fm = FeedManager()

        await fm._aggregate_candle("VEDL", 450.0, 2_000_000)
        await fm._aggregate_candle("TCS", 3800.0, 500_000)

        assert fm._current_candles["VEDL"]["volume"] == 0
        assert fm._current_candles["TCS"]["volume"] == 0
        assert fm._last_vol_today["VEDL"] == 2_000_000
        assert fm._last_vol_today["TCS"] == 500_000


class TestPerSymbolRvolProfile:
    """Issue #2: _enrich_strategy5_params stored the RVOL profile in the shared
    params dict. The first symbol's profile was used for all subsequent symbols.
    Fixed by get_strategy_params() returning a copy — each evaluation gets its
    own dict, so cross-symbol contamination is impossible.
    """

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.get_redis")
    async def test_different_symbols_get_own_profiles(self, mock_get_redis):
        """VEDL and TCS should each load their own RVOL profile from Redis."""
        from app.services.strategy_runner import strategy_runner

        vedl_profile = {"0": 10000.0, "1": 12000.0, "2": 11000.0}
        tcs_profile = {"0": 5000.0, "1": 6000.0, "2": 5500.0}

        mock_redis = AsyncMock()

        async def fake_get(key):
            if "VEDL" in key:
                return json.dumps(vedl_profile)
            elif "TCS" in key:
                return json.dumps(tcs_profile)
            return None

        mock_redis.get = AsyncMock(side_effect=fake_get)
        mock_get_redis.return_value = mock_redis

        vedl_params = {"some_existing_param": True}
        tcs_params = {"some_existing_param": True}

        await strategy_runner._enrich_strategy5_params("VEDL", vedl_params)
        assert vedl_params["_rvol_profile"] is not None
        assert vedl_params["_rvol_profile"]["0"] == 10000.0

        await strategy_runner._enrich_strategy5_params("TCS", tcs_params)
        assert tcs_params["_rvol_profile"] is not None
        assert tcs_params["_rvol_profile"]["0"] == 5000.0
        assert tcs_params["_rvol_profile"] != vedl_params["_rvol_profile"]
