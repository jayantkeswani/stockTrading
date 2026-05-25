"""add signal_generated_at to positions

Revision ID: e86541049aa7
Revises: c1d4e9f27a31
Create Date: 2026-05-25 23:42:33.264298
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e86541049aa7'
down_revision: Union[str, None] = 'c1d4e9f27a31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('signal_generated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('positions', 'signal_generated_at')
