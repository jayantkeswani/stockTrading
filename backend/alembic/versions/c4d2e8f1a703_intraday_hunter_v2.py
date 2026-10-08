"""intraday hunter v2: run variants, learning-loop tables, strategy row + IH-v2 profile

Revision ID: c4d2e8f1a703
Revises: a1c2e3f40b91
Create Date: 2026-10-09 03:00:00.000000

- `intraday_hunter_runs.variant` (existing rows backfilled 'v1'); the unique constraint moves
  from (trading_date) to (trading_date, variant) so v1 and v2 each own one row per day.
- New tables: `ih_minute_log` (the per-minute learning dataset), `ih_teacher_days` (the teacher's
  plan + actual live trade), `ih_day_grades` (nightly grades), `ih_weekly_reviews` (Saturday
  proposals — never auto-applied).
- `strategy_configs` row for `intraday_hunter_v2` (shadow + yolo enabled; not candle-evaluated —
  auto_mode false and no registry class, so is_active is ignored by the candle evaluator) with
  every v2 tunable seeded in `parameters`. `is_active` is v2's RUNTIME KILL SWITCH (seeded true):
  false stops every v2 job/hook within ~15s without a redeploy (params.v2_active).
- `IH-v2` YOLO profile subscribing ONLY to `intraday_hunter_v2`, execution confidence 0 (v2's
  ENTER is the decision; confidence is logged for later calibration, never a gate).

Seeds are idempotent. All paper.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c4d2e8f1a703"
down_revision: Union[str, None] = "a1c2e3f40b91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_V2_PARAMS = {
    "basket_tp_sl_pct": 0.20,
    "basket_time_exit": "11:30",
    "round_hold_enabled": True,
    "round_hold_activate_frac": 0.90,
    "round_hold_giveback_frac": 0.75,
    "round_hold_max_min": 5,
    "round_hold_points": {"NIFTY": 10, "BANKNIFTY": 30, "SENSEX": 30},
    "round_step": {"NIFTY": 100, "BANKNIFTY": 500, "SENSEX": 500},
    "call2_first": "09:16",
    "call2_deadline": "09:25",
    "call2_model": "claude-sonnet-5-5",
    "enforce_plan_gate": False,
    "enforce_oi_gate": False,
    "leg_structure": {"BANKNIFTY": [0, -1], "NIFTY": [0], "SENSEX": [0]},
    "fixed_lots_per_leg": 2,
    "ai_overlay_enabled": False,
    "note": "IH v2: signals from v2 Call 2 ENTER (services/intraday_hunter_v2/signals.py); "
            "basket-level exits in trade_monitor; not candle-evaluated.",
}


def _ts() -> list:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    # 1. run variants
    op.add_column(
        "intraday_hunter_runs",
        sa.Column("variant", sa.String(length=10), server_default=sa.text("'v1'"), nullable=False),
    )
    op.drop_constraint("uq_intraday_hunter_runs_trading_date", "intraday_hunter_runs", type_="unique")
    op.create_unique_constraint(
        "uq_intraday_hunter_runs_date_variant", "intraday_hunter_runs", ["trading_date", "variant"]
    )

    # 2. learning-loop tables
    op.create_table(
        "ih_minute_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("minute_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("index", sa.String(length=20), nullable=False),
        sa.Column("variant", sa.String(length=10), nullable=False),
        sa.Column("features", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("arms", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trading_date", "minute_ts", "index", "variant", name="uq_ih_minute_log"),
    )
    op.create_index("idx_ih_minute_log_date", "ih_minute_log", ["trading_date"])

    op.create_table(
        "ih_teacher_days",
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("plan", jsonb, nullable=True),
        sa.Column("plan_video_id", sa.String(length=32), nullable=True),
        sa.Column("plan_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live", jsonb, nullable=True),
        sa.Column("live_video_id", sa.String(length=32), nullable=True),
        sa.Column("live_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("errors", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("trading_date"),
    )

    op.create_table(
        "ih_day_grades",
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'PRELIM'"), nullable=False),
        sa.Column("market", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("teacher", jsonb, nullable=True),
        sa.Column("v1", jsonb, nullable=True),
        sa.Column("v2", jsonb, nullable=True),
        sa.Column("arms", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("gates", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("lesson", jsonb, nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("trading_date"),
    )

    op.create_table(
        "ih_weekly_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_ending", sa.Date(), nullable=False),
        sa.Column("ledger", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("proposal", jsonb, nullable=True),
        sa.Column("calibration", jsonb, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default=sa.text("'PROPOSED'"), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_ending", name="uq_ih_weekly_reviews_week_ending"),
    )

    # 3. strategy_configs row (ON CONFLICT DO NOTHING — unique on strategy_name)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO strategy_configs
                (id, strategy_name, is_active, parameters, risk_params, symbols, timeframes,
                 auto_mode, symbol_map, shadow_enabled, yolo_enabled, created_at, updated_at)
            VALUES
                (gen_random_uuid(), 'intraday_hunter_v2', true, CAST(:params AS jsonb),
                 '{}'::jsonb, '["NIFTY", "BANKNIFTY", "SENSEX"]'::jsonb, '["1m"]'::jsonb,
                 false, '{}'::jsonb, true, true, now(), now())
            ON CONFLICT (strategy_name) DO NOTHING
            """
        ),
        {"params": json.dumps(_V2_PARAMS)},
    )

    # 4. IH-v2 YOLO profile (no unique on name → NOT EXISTS guard)
    op.execute(
        """
        INSERT INTO yolo_profiles
            (id, name, profit_cap, is_active, sort_order, invalidation_quorum,
             invalidation_strong_only, strategies, setups, min_confidence_for_execution,
             created_at, updated_at)
        SELECT gen_random_uuid(), 'IH-v2', 100000.00, true, 101, false,
               true, '["intraday_hunter_v2"]'::jsonb, '[]'::jsonb, 0,
               now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM yolo_profiles WHERE name = 'IH-v2');
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM yolo_profiles WHERE name = 'IH-v2';")
    op.execute("DELETE FROM strategy_configs WHERE strategy_name = 'intraday_hunter_v2';")
    op.drop_table("ih_weekly_reviews")
    op.drop_table("ih_day_grades")
    op.drop_table("ih_teacher_days")
    op.drop_index("idx_ih_minute_log_date", table_name="ih_minute_log")
    op.drop_table("ih_minute_log")
    op.execute("DELETE FROM intraday_hunter_runs WHERE variant <> 'v1';")
    op.drop_constraint("uq_intraday_hunter_runs_date_variant", "intraday_hunter_runs", type_="unique")
    op.create_unique_constraint(
        "uq_intraday_hunter_runs_trading_date", "intraday_hunter_runs", ["trading_date"]
    )
    op.drop_column("intraday_hunter_runs", "variant")
