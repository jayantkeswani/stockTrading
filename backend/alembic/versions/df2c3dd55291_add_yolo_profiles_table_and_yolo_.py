"""add yolo_profiles table and yolo_profile_id to trades and positions

Revision ID: df2c3dd55291
Revises: 65dff955f4db
Create Date: 2026-05-28 03:01:39.292868
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df2c3dd55291'
down_revision: Union[str, None] = '65dff955f4db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('yolo_profiles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('profit_cap', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_yolo_profiles_is_active', 'yolo_profiles', ['is_active'], unique=False)

    op.add_column('trades', sa.Column('yolo_profile_id', sa.Uuid(), nullable=True))
    op.create_index('idx_trades_yolo_profile_id', 'trades', ['yolo_profile_id'], unique=False)
    op.create_foreign_key('fk_trades_yolo_profile_id', 'trades', 'yolo_profiles', ['yolo_profile_id'], ['id'])

    op.add_column('positions', sa.Column('yolo_profile_id', sa.Uuid(), nullable=True))
    op.create_index('idx_positions_yolo_profile_id', 'positions', ['yolo_profile_id'], unique=False)
    op.create_foreign_key('fk_positions_yolo_profile_id', 'positions', 'yolo_profiles', ['yolo_profile_id'], ['id'])

    # Seed a profile from existing max_daily_profit and backfill YOLO trades
    op.execute("""
        DO $$
        DECLARE
            cap NUMERIC(12,2);
            profile_uuid UUID;
        BEGIN
            SELECT max_daily_profit INTO cap FROM trading_config WHERE id = 1;
            IF cap IS NOT NULL AND cap > 0 THEN
                profile_uuid := gen_random_uuid();
                INSERT INTO yolo_profiles (id, name, profit_cap, is_active, sort_order)
                VALUES (profile_uuid, CONCAT(ROUND(cap/1000)::TEXT, 'K'), cap, TRUE, 0);

                UPDATE trades SET yolo_profile_id = profile_uuid WHERE source = 'YOLO';
                UPDATE positions SET yolo_profile_id = profile_uuid
                WHERE trade_id IN (SELECT id FROM trades WHERE source = 'YOLO');

                UPDATE trading_config SET max_daily_profit = 0 WHERE id = 1;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Restore max_daily_profit from the seeded profile before dropping
    op.execute("""
        DO $$
        DECLARE
            cap NUMERIC(12,2);
        BEGIN
            SELECT profit_cap INTO cap FROM yolo_profiles ORDER BY sort_order LIMIT 1;
            IF cap IS NOT NULL THEN
                UPDATE trading_config SET max_daily_profit = cap WHERE id = 1;
            END IF;
        END $$;
    """)

    op.drop_constraint('fk_positions_yolo_profile_id', 'positions', type_='foreignkey')
    op.drop_index('idx_positions_yolo_profile_id', table_name='positions')
    op.drop_column('positions', 'yolo_profile_id')

    op.drop_constraint('fk_trades_yolo_profile_id', 'trades', type_='foreignkey')
    op.drop_index('idx_trades_yolo_profile_id', table_name='trades')
    op.drop_column('trades', 'yolo_profile_id')

    op.drop_index('idx_yolo_profiles_is_active', table_name='yolo_profiles')
    op.drop_table('yolo_profiles')
