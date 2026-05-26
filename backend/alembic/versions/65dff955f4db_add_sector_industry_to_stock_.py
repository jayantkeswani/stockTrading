"""add sector industry to stock_fundamentals

Revision ID: 65dff955f4db
Revises: e86541049aa7
Create Date: 2026-05-26 15:25:11.332242
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65dff955f4db'
down_revision: Union[str, None] = 'e86541049aa7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_fundamentals', sa.Column('sector', sa.String(length=100), nullable=True))
    op.add_column('stock_fundamentals', sa.Column('industry', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('stock_fundamentals', 'industry')
    op.drop_column('stock_fundamentals', 'sector')
