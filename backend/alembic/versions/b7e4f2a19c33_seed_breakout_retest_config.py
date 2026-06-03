"""seed breakout_retest strategy config

Seeds the Strategy 6 (Breakout-Retest) strategy_configs row. Inactive by default
(is_active=false) so it ships dark; symbols come dynamically from the shared S5
screener watchlist (strategy.get_symbols), so the static symbols list stays empty.

Revision ID: b7e4f2a19c33
Revises: a3f1c8e9b27d
Create Date: 2026-06-04 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4f2a19c33"
down_revision: str | None = "a3f1c8e9b27d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
                'breakout_retest',
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
    op.execute(sa.text("DELETE FROM strategy_configs WHERE strategy_name = 'breakout_retest'"))
