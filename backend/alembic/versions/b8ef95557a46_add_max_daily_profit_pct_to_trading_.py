"""add max_daily_profit to trading_config

Revision ID: b8ef95557a46
Revises: a003_remove_signal_sizing
Create Date: 2026-05-22 20:08:23.991244
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8ef95557a46'
down_revision: Union[str, None] = 'a003_remove_signal_sizing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trading_config', sa.Column('max_daily_profit', sa.Numeric(precision=12, scale=2), server_default=sa.text('0.00'), nullable=False))


def downgrade() -> None:
    op.drop_column('trading_config', 'max_daily_profit')
