"""add thesis-invalidation exit settings to yolo_profiles

Revision ID: a3f1c8e9b27d
Revises: 26d31a3470d8
Create Date: 2026-06-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c8e9b27d'
down_revision: Union[str, None] = '26d31a3470d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'yolo_profiles',
        sa.Column('invalidation_persist', sa.Integer(), nullable=True),
    )
    op.add_column(
        'yolo_profiles',
        sa.Column(
            'invalidation_quorum',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'yolo_profiles',
        sa.Column(
            'invalidation_strong_only',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column('yolo_profiles', 'invalidation_strong_only')
    op.drop_column('yolo_profiles', 'invalidation_quorum')
    op.drop_column('yolo_profiles', 'invalidation_persist')
