"""seed intraday_hunter strategy_configs row + a fixed-qty YOLO profile

Revision ID: a1c2e3f40b91
Revises: f3d9a2c14e88
Create Date: 2026-06-29 21:30:00.000000

Wires the Intraday Hunter agent into the signal/execution pipeline:
- a `strategy_configs` row for `intraday_hunter` (shadow + yolo enabled; is_active/auto_mode
  False — it is NOT candle-evaluated, signals come from the Call 2 ENTER path). `parameters`
  carries the index-anchored 1:1 SL/target band + the fixed-lots basket for reference.
- a `IntradayHunter` YOLO profile subscribing to `strategies=["intraday_hunter"]`, with a
  lower execution-confidence bar (40 — IH confidence runs ~60). profit_cap is set high
  (effectively non-binding; per-leg 1:1 targets do the booking) — tune profit/loss caps in
  Settings → YOLO Profiles. sort_order 100 keeps it off the protected default-profile slot.

Both inserts are idempotent (skip if already present). All paper.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a1c2e3f40b91"
down_revision: Union[str, None] = "f3d9a2c14e88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. strategy_configs row (unique on strategy_name → ON CONFLICT DO NOTHING)
    op.execute(
        """
        INSERT INTO strategy_configs
            (id, strategy_name, is_active, parameters, risk_params, symbols, timeframes,
             auto_mode, symbol_map, shadow_enabled, yolo_enabled, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'intraday_hunter', false,
             '{"stop_pct": 0.004, "rr": 1.0, "premium_sl_min": 0.20, "premium_sl_max": 0.35,
               "fixed_lots": {"BANKNIFTY": 4, "NIFTY": 2, "SENSEX": 2}, "ai_overlay_enabled": false,
               "note": "Signals emitted from Call 2 ENTER (services/intraday_hunter/signals.py); not candle-evaluated."}'::jsonb,
             '{}'::jsonb, '["NIFTY", "BANKNIFTY", "SENSEX"]'::jsonb, '["1m"]'::jsonb,
             false, '{}'::jsonb, true, true, now(), now())
        ON CONFLICT (strategy_name) DO NOTHING;
        """
    )

    # 2. fixed-qty YOLO profile (no unique on name → guard with NOT EXISTS)
    op.execute(
        """
        INSERT INTO yolo_profiles
            (id, name, profit_cap, is_active, sort_order, invalidation_quorum,
             invalidation_strong_only, strategies, setups, min_confidence_for_execution,
             created_at, updated_at)
        SELECT gen_random_uuid(), 'IntradayHunter', 100000.00, true, 100, false,
               true, '["intraday_hunter"]'::jsonb, '[]'::jsonb, 40,
               now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM yolo_profiles WHERE name = 'IntradayHunter');
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM yolo_profiles WHERE name = 'IntradayHunter';")
    op.execute("DELETE FROM strategy_configs WHERE strategy_name = 'intraday_hunter';")
