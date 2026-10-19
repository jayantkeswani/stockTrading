"""IH v2: Call 2 deadline 09:30 -> 09:45 in the stored strategy params.

The prod `strategy_configs.parameters` row for intraday_hunter_v2 pins call2_deadline, which
overrides the code default, so the new default alone would not move it. Only a row still at the
old value is changed; downgrade restores it the same way.

Revision ID: f5b2d8c1a946
Revises: e2a7c5d9f310
Create Date: 2026-10-19
"""
from alembic import op

revision = "f5b2d8c1a946"
down_revision = "e2a7c5d9f310"
branch_labels = None
depends_on = None


def _set(old: str, new: str) -> None:
    op.execute(
        "UPDATE strategy_configs "
        f"SET parameters = jsonb_set(parameters, '{{call2_deadline}}', '\"{new}\"') "
        "WHERE strategy_name = 'intraday_hunter_v2' "
        f"AND parameters->>'call2_deadline' = '{old}'"
    )


def upgrade() -> None:
    _set("09:30", "09:45")


def downgrade() -> None:
    _set("09:45", "09:30")
