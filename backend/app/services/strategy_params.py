"""Per-strategy parameter service.

Loads strategy-tunable parameters from `strategy_configs.parameters` JSONB,
merges with hardcoded defaults, validates confidence tier ordering, and caches.
"""

import logging
from datetime import time

from app.core.constants import (
    CANSLIM_BREAKOUT_VOLUME_MULTIPLIER,
    CANSLIM_MAX_VIX,
    CANSLIM_MIN_TOTAL_SCORE,
    CANSLIM_SL_PCT,
    CANSLIM_TARGET_PCT,
    CANSLIM_TRAILING_SL_ACTIVATION_PCT,
    DEAD_ZONE_END,
    DEAD_ZONE_START,
    DEFAULT_TARGET_MULTIPLIER,
    VIX_EXTREME,
    VWAP_MIN_DISTANCE_PCT,
    VWAP_PROXIMITY_PCT,
    WINDOW_1_END,
    WINDOW_1_START,
    WINDOW_2_END,
    WINDOW_2_START,
)

logger = logging.getLogger(__name__)

VWAP_DEFAULTS: dict = {
    "trading_windows": [
        {"start": "09:45", "end": "11:00"},
        {"start": "13:45", "end": "14:45"},
    ],
    "dead_zone": {"start": "11:30", "end": "13:30"},
    "vwap_proximity_pct": VWAP_PROXIMITY_PCT,
    "vwap_min_distance_pct": VWAP_MIN_DISTANCE_PCT,
    "sl_pct_aligned": 0.30,
    "sl_pct_unaligned": 0.35,
    "default_target_multiplier": DEFAULT_TARGET_MULTIPLIER,
    "vix_extreme": VIX_EXTREME,
}

CANSLIM_DEFAULTS: dict = {
    "sl_pct": CANSLIM_SL_PCT,
    "target_pct": CANSLIM_TARGET_PCT,
    "min_total_score": CANSLIM_MIN_TOTAL_SCORE,
    "max_vix": CANSLIM_MAX_VIX,
    "breakout_volume_multiplier": CANSLIM_BREAKOUT_VOLUME_MULTIPLIER,
    "trailing_sl_activation_pct": CANSLIM_TRAILING_SL_ACTIVATION_PCT,
}

INTRADAY_FUTURES_DEFAULTS: dict = {
    "trailing_sl_enabled": True,
    "trailing_sl_breakeven_pct": 0.5,
    "trailing_sl_trail_pct": 0.3,
    "rvol_threshold": 1.5,
    "rvol_caution_zone_threshold": 2.5,
    "enabled_setups": ["ORB", "VWAP_BOUNCE", "PDH_PDL", "GAP_CONTINUATION"],
    "min_adr": 1.5,
    "min_orb_range_pct": 0.4,
    "max_orb_range_pct": 2.0,
}

BREAKOUT_RETEST_DEFAULTS: dict = {
    # Which broken levels to arm on. ORB high/low, prev-day high/low, intraday swings.
    "enabled_levels": ["ORB", "PDH_PDL", "SWING"],
    # Breakout / retest geometry (all percentages of price).
    "min_breakout_ext_pct": 0.05,   # 5m close must clear the level by this to arm
    "retest_proximity_pct": 0.15,   # 1m pullback must return within this of the level
    "reclaim_buffer_pct": 0.0,      # reclaim candle must close beyond level by this
    "slice_buffer_pct": 0.20,       # 1m close this far the wrong side = breakout failed → abort
    "max_wait_minutes": 30,         # arm expires if no fire within this many 1m candles
    # Reclaim volume confirmation (reclaim 1m candle vs recent 1m average).
    "reclaim_vol_mult": 1.5,
    "reclaim_vol_lookback": 20,
    # Swing pivot detection (on 5m candles).
    "swing_pivot_left": 2,
    "swing_pivot_right": 2,
    # SL/target. SL just past the retest swing; tight by design.
    "sl_swing_buffer_pct": 0.15,
    "sl_atr_mult": 0.3,
    "min_risk_pct": 0.10,           # whipsaw floor — widen SL if tighter than this
    "max_risk_pct": 1.5,            # skip if risk wider than this (geometry broke)
    "rr_multiplier": 1.8,
    "min_rr": 1.5,
    # Time-of-day hard gate + late-day size-down.
    "arm_start": "09:30",
    "arm_cutoff": "13:30",
    "size_down_after": "12:30",
    "size_down_mult": 0.5,
    # Regime gates (sign checks, with deadbands — never opposing).
    "require_with_nifty_trend": True,
    "nifty_flat_deadband_pct": 0.10,
    "block_opposing_stock_bias": True,
    "stock_bias_deadband": 0.15,
    # Base filters.
    "min_adr": 1.5,
    "min_price": 100.0,
    "max_lots": 2,
}

_STRATEGY_DEFAULTS: dict[str, dict] = {
    "vwap_pullback": VWAP_DEFAULTS,
    "can_slim": CANSLIM_DEFAULTS,
    "intraday_futures": INTRADAY_FUTURES_DEFAULTS,
    "breakout_retest": BREAKOUT_RETEST_DEFAULTS,
}

# In-memory cache: strategy_name -> merged params
_cache: dict[str, dict] = {}


async def get_strategy_params(strategy_name: str, session=None) -> dict:
    """Load strategy parameters, merging DB values over defaults.

    Uses an in-memory cache. Call `clear_strategy_params_cache` after DB updates.
    If session is None, opens its own session.
    """
    if strategy_name in _cache:
        return {**_cache[strategy_name]}

    defaults = _STRATEGY_DEFAULTS.get(strategy_name, {})
    db_params = await _load_from_db(strategy_name, session)

    merged = {**defaults, **db_params}
    _cache[strategy_name] = merged
    return {**merged}


def get_strategy_params_sync(strategy_name: str) -> dict:
    """Return cached params or defaults (no DB call). For use in sync code paths."""
    if strategy_name in _cache:
        return _cache[strategy_name]
    return _STRATEGY_DEFAULTS.get(strategy_name, {})


def clear_strategy_params_cache(strategy_name: str | None = None) -> None:
    """Invalidate cached params. Called when API updates strategy config."""
    if strategy_name:
        _cache.pop(strategy_name, None)
    else:
        _cache.clear()


def get_defaults_for_strategy(strategy_name: str) -> dict:
    """Return the default parameter schema for a strategy (for frontend forms)."""
    return dict(_STRATEGY_DEFAULTS.get(strategy_name, {}))


def parse_trading_windows(params: dict) -> list[tuple[time, time]]:
    """Parse trading_windows from params into list of (start, end) time tuples."""
    windows = params.get("trading_windows")
    if not windows:
        return []
    result = []
    for w in windows:
        start = _parse_time(w.get("start", ""))
        end = _parse_time(w.get("end", ""))
        if start and end:
            result.append((start, end))
    return result


def parse_dead_zone(params: dict) -> tuple[time, time] | None:
    """Parse dead_zone from params into a (start, end) time tuple."""
    dz = params.get("dead_zone")
    if not dz:
        return None
    start = _parse_time(dz.get("start", ""))
    end = _parse_time(dz.get("end", ""))
    if start and end:
        return (start, end)
    return None


async def _load_from_db(strategy_name: str, session=None) -> dict:
    """Load parameters JSONB from strategy_configs table.

    Uses the provided session if given, otherwise opens its own.
    Returns an empty dict on miss or on any DB error (caller merges with defaults).
    """
    try:
        from sqlalchemy import select as sa_select
        from app.models.strategy_config import StrategyConfig

        if session:
            result = await session.execute(
                sa_select(StrategyConfig.parameters).where(
                    StrategyConfig.strategy_name == strategy_name
                )
            )
            params = result.scalar_one_or_none()
            return params or {}

        from app.core.database import async_session_factory
        async with async_session_factory() as s:
            result = await s.execute(
                sa_select(StrategyConfig.parameters).where(
                    StrategyConfig.strategy_name == strategy_name
                )
            )
            params = result.scalar_one_or_none()
            return params or {}
    except Exception:
        logger.exception("Failed to load strategy params for %s — using defaults", strategy_name)
        return {}



def _parse_time(s: str) -> time | None:
    """Parse an 'HH:MM' string to a time object. Returns None on invalid input."""
    if not s:
        return None
    try:
        parts = s.split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None
