"""add fill_model to trading_config and fill_model/fill_meta to trades

Revision ID: a4c8e1f3b906
Revises: b7e2d4a9c103
Create Date: 2026-06-11 21:00:00.000000

Bid/ask paper fill model. trading_config.fill_model defaults to BID_ASK (the new
regime); trades.fill_model stamps the regime that filled each trade (existing rows
stay NULL = pre-cutover LTP fills); trades.fill_meta records the per-fill quote
snapshot (entry + exit) for the paper→live slippage dataset.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "a4c8e1f3b906"
down_revision: Union[str, None] = "b7e2d4a9c103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trading_config",
        sa.Column(
            "fill_model", sa.String(10), nullable=False, server_default=sa.text("'BID_ASK'")
        ),
    )
    op.add_column("trades", sa.Column("fill_model", sa.String(10), nullable=True))
    op.add_column("trades", sa.Column("fill_meta", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "fill_meta")
    op.drop_column("trades", "fill_model")
    op.drop_column("trading_config", "fill_model")
