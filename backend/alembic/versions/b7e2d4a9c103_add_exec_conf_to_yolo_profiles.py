"""add per-profile min_confidence_for_execution to yolo_profiles

Revision ID: b7e2d4a9c103
Revises: d9a3f6b1e207
Create Date: 2026-06-08 01:30:00.000000

NULL = inherit the global trading_config.min_confidence_for_execution (existing
profiles migrate as NULL, so behaviour is unchanged until an override is set).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2d4a9c103"
down_revision: Union[str, None] = "d9a3f6b1e207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "yolo_profiles",
        sa.Column("min_confidence_for_execution", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("yolo_profiles", "min_confidence_for_execution")
