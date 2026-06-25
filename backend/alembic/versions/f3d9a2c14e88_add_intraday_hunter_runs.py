"""add intraday_hunter_runs table

Revision ID: f3d9a2c14e88
Revises: e2b1c7d4f309
Create Date: 2026-06-24 09:00:00.000000

One row per trading day for the Intraday Hunter agent (Call 1 thesis + Call 2 decisions +
chart paths + denormalized decision/direction/confidence + post-hoc outcome). See
docs/ai/intraday-hunter-agent.md §Data model.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3d9a2c14e88"
down_revision: Union[str, None] = "e2b1c7d4f309"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intraday_hunter_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "is_expiry", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("expiry_index", sa.String(length=20), nullable=True),
        sa.Column("call1_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "call1_chart_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("call2_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "call2_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "call2_chart_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("decision", sa.String(length=10), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("outcome_played_out", sa.Boolean(), nullable=True),
        sa.Column("realized_outcome_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trading_date", name="uq_intraday_hunter_runs_trading_date"),
    )
    op.create_index(
        "idx_intraday_hunter_runs_trading_date",
        "intraday_hunter_runs",
        ["trading_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_intraday_hunter_runs_trading_date", table_name="intraday_hunter_runs"
    )
    op.drop_table("intraday_hunter_runs")
