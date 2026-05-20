"""add min_confidence_to_persist to trading_config

Revision ID: 2d30e4f1db62
Revises: 3e6563dffeb2
Create Date: 2026-05-21 01:03:35.655702
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d30e4f1db62'
down_revision: Union[str, None] = '3e6563dffeb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trading_config",
        sa.Column(
            "min_confidence_to_persist",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="30.00",
        ),
    )


def downgrade() -> None:
    op.drop_column("trading_config", "min_confidence_to_persist")
