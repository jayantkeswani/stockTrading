"""add shadow_enabled and yolo_enabled to strategy_configs

Revision ID: c1d4e9f27a31
Revises: b8ef95557a46
Create Date: 2026-05-23 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d4e9f27a31'
down_revision: Union[str, None] = 'b8ef95557a46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('strategy_configs', sa.Column('shadow_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('strategy_configs', sa.Column('yolo_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))


def downgrade() -> None:
    op.drop_column('strategy_configs', 'yolo_enabled')
    op.drop_column('strategy_configs', 'shadow_enabled')
