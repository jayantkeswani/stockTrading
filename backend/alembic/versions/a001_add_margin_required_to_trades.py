"""add margin_required to trades

Revision ID: a001_margin_trades
Revises: 14c22917c90b
Create Date: 2026-05-22
"""
from alembic import op
from sqlalchemy import text
import sqlalchemy as sa

revision = "a001_margin_trades"
down_revision = "14c22917c90b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("margin_required", sa.Numeric(12, 2), nullable=True))
    # Backfill existing futures trades: estimated margin ~18% of contract value
    op.execute(text("""
        UPDATE trades
        SET margin_required = entry_price * quantity * 0.18
        WHERE option_type IS NULL AND margin_required IS NULL
    """))
    # Backfill existing option trades: margin = premium paid
    op.execute(text("""
        UPDATE trades
        SET margin_required = entry_price * quantity
        WHERE option_type IS NOT NULL AND margin_required IS NULL
    """))


def downgrade() -> None:
    op.drop_column("trades", "margin_required")
