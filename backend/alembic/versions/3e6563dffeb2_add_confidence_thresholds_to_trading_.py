"""add confidence thresholds to trading config

Revision ID: 3e6563dffeb2
Revises: d8adab1c6bd4
Create Date: 2026-05-21 00:46:40.452801
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e6563dffeb2'
down_revision: Union[str, None] = 'd8adab1c6bd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trading_config",
        sa.Column(
            "min_confidence_for_shadow",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="70.00",
        ),
    )
    op.add_column(
        "trading_config",
        sa.Column(
            "min_confidence_for_execution",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="70.00",
        ),
    )


def downgrade() -> None:
    op.drop_column("trading_config", "min_confidence_for_execution")
    op.drop_column("trading_config", "min_confidence_for_shadow")
