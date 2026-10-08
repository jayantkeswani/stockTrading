"""Intraday Hunter v2 tunables — the single source of defaults.

Live values are `strategy_configs.parameters` for `intraday_hunter_v2` (seeded by migration
`c4d2e8f1a703`, editable in the DB / Settings), merged over these defaults by
`services/strategy_params.get_strategy_params(_sync)`. They will be recalibrated from the
parallel exit-behaviour study — never hard-code these numbers elsewhere; read them via
`v2_params()` / `v2_params_async()`.
"""
from __future__ import annotations

from datetime import time

STRATEGY = "intraday_hunter_v2"
VARIANT = "v2"

INTRADAY_HUNTER_V2_DEFAULTS: dict = {
    # ── Basket-level exit (trade_monitor._check_ih_v2_baskets) ──
    # Symmetric 1:1 on rupees: close ALL legs when basket MTM >= +T or <= -T,
    # T = basket_tp_sl_pct x basket cost (sum entry premium x qty). Exit-side (bid) valuation.
    "basket_tp_sl_pct": 0.20,
    "basket_time_exit": "11:30",          # backstop IST: close all legs if neither +T nor -T hit
    # Round-number hold: at MTM >= +0.9T with the majority of traded indices within
    # round_hold_points of a round number IN the trade direction, hold for the touch.
    "round_hold_enabled": True,
    "round_hold_activate_frac": 0.90,     # activate at MTM >= 0.9T
    "round_hold_giveback_frac": 0.75,     # exit if MTM gives back to 0.75T
    "round_hold_max_min": 5,              # exit after N minutes regardless
    "round_hold_points": {"NIFTY": 10, "BANKNIFTY": 30, "SENSEX": 30},
    "round_step": {"NIFTY": 100, "BANKNIFTY": 500, "SENSEX": 500},
    # ── Call 2 cadence (wall-clock decision times, IST) ──
    "call2_first": "09:16",               # first decision fires on the 09:15 candle close
    "call2_deadline": "09:25",            # last possible decision; WAIT at the deadline -> SKIP
    "call2_model": None,                  # None -> settings.intraday_hunter_v2_call2_model
    "call2_timeout_s": 60,
    "call1_model": None,                  # None -> llm_cli.MODEL (Opus, charts attached)
    # ── Gates: SHADOW-ONLY for the first 20 trading days (compute + log, never block) ──
    "enforce_plan_gate": False,
    "enforce_oi_gate": False,
    "oi_flow_deadband": 0.0,              # |flow| <= deadband -> oi_flow_side = none
    # ── Levels ──
    "opening_type_gap_pct": 0.15,         # basket-average gap: >= +x gap-up, <= -x gap-down
    "opening_range_end": "09:19",         # opening range = 09:15..09:19 candles inclusive
    # ── Basket shape (His shape: BN ATM + 1 OTM, NIFTY ATM, SENSEX ATM). depth<0 = OTM. ──
    "leg_structure": {"BANKNIFTY": [0, -1], "NIFTY": [0], "SENSEX": [0]},
    # ── Minute log + capture ──
    "minute_log_start": "09:15",
    "minute_log_end": "10:45",            # plus every minute while any v2 position is open
    "capture_strikes_each_side": 2,       # ATM +/- 2, CE and PE, per index
    "oi_hf_start": "09:15",
    "oi_hf_end": "09:45",
    # ── Learning loop ──
    "lessons_n": 8,                       # last N graded lessons fed into v2 Call 1
    "grade_barriers": [[0.25, 0.20], [0.30, 0.25]],  # (target%, stop%) first-touch label sets
    "gate_shadow_days": 20,
    "ai_overlay_enabled": False,          # v2 signals never go through the Gemini overlay
}


def parse_hhmm(v: str | time) -> time:
    """'HH:MM' -> time (passes a time through)."""
    if isinstance(v, time):
        return v
    hh, mm = str(v).split(":")[:2]
    return time(int(hh), int(mm))


def v2_params() -> dict:
    """Sync (cache-only) merged v2 params — safe on hot paths like the 500ms monitor."""
    from app.services.strategy_params import get_strategy_params_sync

    p = get_strategy_params_sync(STRATEGY)
    return {**INTRADAY_HUNTER_V2_DEFAULTS, **(p or {})}


async def v2_params_async() -> dict:
    """Async merged v2 params (loads + caches the DB row on first use)."""
    from app.services.strategy_params import get_strategy_params

    p = await get_strategy_params(STRATEGY)
    return {**INTRADAY_HUNTER_V2_DEFAULTS, **(p or {})}


def call2_model(params: dict) -> str:
    """Effective Call 2 model: params override, else the settings default."""
    from app.config import settings

    return params.get("call2_model") or settings.intraday_hunter_v2_call2_model


# ─────────────────────────── runtime kill switch ───────────────────────────
# `strategy_configs.is_active` for intraday_hunter_v2 (seeded TRUE) is the no-redeploy kill switch:
# set it false (PUT /api/v1/strategies/intraday_hunter_v2 {"is_active": false}, the Settings page,
# or SQL) and within ~15s every v2 entry point stops — scheduler jobs, the Call 2 watcher, the
# minute log, the ATM±2 capture and signal emission. OPEN v2 positions are still exited by the
# basket monitor (a kill never orphans a basket). `settings.intraday_hunter_v2_enabled` (env) is
# the deploy-level hard off. Fails CLOSED: a DB error reads as inactive.
_ACTIVE_TTL_S = 15.0
_active_cache: tuple[float, bool] | None = None


async def v2_active(fail_closed: bool = True) -> bool:
    """True when v2 may act now (env flag AND strategy_configs.is_active, 15s TTL).

    `fail_closed=False` (signal emission after a decided ENTER) stops only on an EXPLICIT off:
    on a DB error it returns the last known value (True if none) instead of False.
    """
    global _active_cache
    import time as _t

    from app.config import settings

    if not settings.intraday_hunter_v2_enabled:
        return False
    now = _t.monotonic()
    if _active_cache and now - _active_cache[0] < _ACTIVE_TTL_S:
        return _active_cache[1]
    try:
        from sqlalchemy import select

        from app.core.database import async_session_factory
        from app.models.strategy_config import StrategyConfig

        async with async_session_factory() as session:
            val = (await session.execute(
                select(StrategyConfig.is_active).where(StrategyConfig.strategy_name == STRATEGY)
            )).scalar_one_or_none()
        active = bool(val)
    except Exception:  # noqa: BLE001
        if not fail_closed:
            return _active_cache[1] if _active_cache else True
        active = False
    _active_cache = (now, active)
    return active


def reset_active_cache() -> None:
    """Drop the kill-switch cache (tests / immediate re-read)."""
    global _active_cache
    _active_cache = None
