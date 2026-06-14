"""add min_bias_strength gate to yolo_profiles

Revision ID: d9a4f1c20b73
Revises: a4c8e1f3b906
Create Date: 2026-06-15 10:30:00.000000

Per-profile intraday-bias strength gate. NULL = no gate (backward compatible — all
existing profiles keep acting on every bias). When set (WEAK/MODERATE/STRONG) the profile
only executes signals whose stored stock intraday_bias.strength meets the bar — the S5
PDH_PDL/ORB + STRONG-bias book.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9a4f1c20b73"
down_revision: Union[str, None] = "a4c8e1f3b906"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("yolo_profiles", sa.Column("min_bias_strength", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("yolo_profiles", "min_bias_strength")
