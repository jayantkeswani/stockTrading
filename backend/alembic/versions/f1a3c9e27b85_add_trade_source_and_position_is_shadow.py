"""add trade source and position is_shadow

Revision ID: f1a3c9e27b85
Revises: d79b95128c64
Create Date: 2026-04-24 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a3c9e27b85"
down_revision: str | None = "d79b95128c64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("source", sa.String(20), nullable=False, server_default="MANUAL"))
    op.create_index("idx_trades_source", "trades", ["source"])

    op.add_column("positions", sa.Column("is_shadow", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("idx_positions_is_shadow", "positions", ["is_shadow"])

    # Backfill: trades executed by the YOLO agent get source=YOLO
    op.execute("""
        UPDATE trades
        SET source = 'YOLO'
        WHERE id IN (
            SELECT DISTINCT trade_id
            FROM agent_logs
            WHERE action_type = 'AUTO_EXECUTED'
              AND trade_id IS NOT NULL
        )
    """)


def downgrade() -> None:
    op.drop_index("idx_trades_source", table_name="trades")
    op.drop_column("trades", "source")

    op.drop_index("idx_positions_is_shadow", table_name="positions")
    op.drop_column("positions", "is_shadow")
