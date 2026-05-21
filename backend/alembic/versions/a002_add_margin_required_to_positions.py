"""add margin_required to positions

Revision ID: a002_margin_positions
Revises: a001_margin_trades
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "a002_margin_positions"
down_revision = "a001_margin_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("margin_required", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "margin_required")
