"""add ai_overlay_enabled master toggle to trading_config

The AI confidence overlay (an up-to-25s LLM call per signal, between signal
generation and execution) can now be turned off globally via this master flag,
and per-strategy via `strategy_configs.parameters.ai_overlay_enabled` (JSONB,
default true). The overlay runs only when master AND the per-strategy flag are on.
Disabling it removes the execution lag (which erodes tight-entry strategies like S6).

Revision ID: d9a3f6b1e207
Revises: c8f5a1e09b44
Create Date: 2026-06-04 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9a3f6b1e207"
down_revision: str | None = "c8f5a1e09b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trading_config",
        sa.Column("ai_overlay_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("trading_config", "ai_overlay_enabled")
