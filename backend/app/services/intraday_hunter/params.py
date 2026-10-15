"""Intraday Hunter v1 tunables — basket-level exit defaults.

v1 legs enter together on a Call 2 ENTER and exit TOGETHER on basket MTM ±T (the same pure
rules as v2: `intraday_hunter_v2.basket.evaluate_basket`, run by `trade_monitor._check_ih_baskets`).
Live values are `strategy_configs.parameters` for `intraday_hunter`, merged over these defaults.
v1 keeps its OWN copy of the basket params (seeded with v2's 2026-10-09 rupee table) so a v2
learning-loop promotion never moves v1.
"""
from __future__ import annotations

STRATEGY = "intraday_hunter"

INTRADAY_HUNTER_DEFAULTS: dict = {
    # T = sum over the legs traded of lots x rupees_per_lot[index] (fixed rupee book, 1:1) —
    # the 2-lot YOLO basket (BN ITM-2 + ITM-1, NIFTY, SENSEX) ≈ ₹20.7K, the 1-lot shadow half.
    "basket_t_mode": "rupees",
    "rupees_per_lot": {"BANKNIFTY": 3915, "NIFTY": 1640, "SENSEX": 862},
    # Used for T only when basket_t_mode="pct"; always used for the legs' informational
    # stop_loss/target_price (premium x (1 ∓ pct)).
    "basket_tp_sl_pct": 0.15,
    "basket_time_exit": "11:30",          # backstop IST: close all legs if neither +T nor -T hit
    "round_hold_enabled": False,          # v2's round-number hold is not used by v1
}


async def v1_params_async() -> dict:
    """Async merged v1 params (loads + caches the DB row on first use)."""
    from app.services.strategy_params import get_strategy_params

    return {**INTRADAY_HUNTER_DEFAULTS, **((await get_strategy_params(STRATEGY)) or {})}
