"""add strategy + setup selection to yolo_profiles

Adds `strategies` and `setups` JSONB list columns to `yolo_profiles`. Empty list
(the default) = "act on all", so existing profiles are unchanged. A profile only
executes a signal when (strategies empty OR signal.strategy_name in strategies)
AND (setups empty OR signal.indicators.setup_type in setups) — the execution-side
filter that lets one paper book run a full-vs-subset A/B from a single signal stream.

Revision ID: c8f5a1e09b44
Revises: b7e4f2a19c33
Create Date: 2026-06-04 11:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8f5a1e09b44"
down_revision: str | None = "b7e4f2a19c33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "yolo_profiles",
        sa.Column("strategies", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "yolo_profiles",
        sa.Column("setups", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("yolo_profiles", "setups")
    op.drop_column("yolo_profiles", "strategies")
