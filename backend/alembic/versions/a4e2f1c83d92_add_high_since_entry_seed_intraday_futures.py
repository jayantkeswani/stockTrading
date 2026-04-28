"""add high_since_entry, seed intraday_futures config, widen oi option_type

Revision ID: a4e2f1c83d92
Revises: f1a3c9e27b85
Create Date: 2026-04-27 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4e2f1c83d92"
down_revision: str | None = "f1a3c9e27b85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("high_since_entry", sa.Numeric(10, 2), nullable=True))

    op.alter_column(
        "oi_snapshots", "option_type",
        existing_type=sa.String(2),
        type_=sa.String(3),
        existing_nullable=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO strategy_configs (
                id, strategy_name, is_active, auto_mode,
                symbols, parameters, risk_params, timeframes, symbol_map,
                created_at, updated_at
            )
            VALUES (
                gen_random_uuid(),
                'intraday_futures',
                false,
                true,
                '[]'::jsonb,
                '{}'::jsonb,
                '{}'::jsonb,
                '[]'::jsonb,
                '{}'::jsonb,
                now(),
                now()
            )
            ON CONFLICT (strategy_name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM strategy_configs WHERE strategy_name = 'intraday_futures'"))

    op.alter_column(
        "oi_snapshots", "option_type",
        existing_type=sa.String(3),
        type_=sa.String(2),
        existing_nullable=False,
    )

    op.drop_column("positions", "high_since_entry")
