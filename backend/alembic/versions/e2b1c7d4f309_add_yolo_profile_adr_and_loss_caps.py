"""add min_adr, loss_cap, per_lot_loss_stop to yolo_profiles

Revision ID: e2b1c7d4f309
Revises: d9a4f1c20b73
Create Date: 2026-06-18 00:45:00.000000

All three are NULL by default, so existing profiles migrate unchanged:
- min_adr            NULL = no ADR execution filter
- loss_cap           NULL = no daily loss cap
- per_lot_loss_stop  NULL = no per-lot MTM loss stop
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2b1c7d4f309"
down_revision: Union[str, None] = "d9a4f1c20b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("yolo_profiles", sa.Column("min_adr", sa.Numeric(5, 2), nullable=True))
    op.add_column("yolo_profiles", sa.Column("loss_cap", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "yolo_profiles", sa.Column("per_lot_loss_stop", sa.Numeric(12, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("yolo_profiles", "per_lot_loss_stop")
    op.drop_column("yolo_profiles", "loss_cap")
    op.drop_column("yolo_profiles", "min_adr")
