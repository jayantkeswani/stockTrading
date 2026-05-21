"""remove lots, quantity, sizing_meta from signals

Revision ID: a003_remove_signal_sizing
Revises: a002_margin_positions
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a003_remove_signal_sizing"
down_revision = "a002_margin_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("signals", "lots")
    op.drop_column("signals", "quantity")
    op.drop_column("signals", "sizing_meta")


def downgrade() -> None:
    op.add_column("signals", sa.Column("lots", sa.Integer(), nullable=True))
    op.add_column("signals", sa.Column("quantity", sa.Integer(), nullable=True))
    op.add_column("signals", sa.Column("sizing_meta", JSONB(), nullable=True))
