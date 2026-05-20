"""Test isolation fixtures for test_services.

Clears StrategyRunner's per-candle in-memory caches before each test so that
singleton state from one test doesn't leak into the next.
"""

import pytest


@pytest.fixture(autouse=True)
def clear_strategy_runner_caches():
    """Reset all per-candle caches on the StrategyRunner singleton before each test."""
    from app.services.strategy_runner import strategy_runner

    strategy_runner._s5_shift_last_checked.clear()
    strategy_runner._s5_session_cache.clear()
    strategy_runner._s5_oi_cache.clear()
    strategy_runner._s5_counts_cache = None
    strategy_runner._s5_rvol_profiles.clear()
    strategy_runner._oi_analysis_cache.clear()
    strategy_runner._daily_candles_cache.clear()
    strategy_runner._canslim_symbol_cache.clear()

    yield
