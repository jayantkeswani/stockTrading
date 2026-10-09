"""IH v2 weekly-review approval flow: per-proposal lifecycle + challenger table.

Revision ID: d7e3f9a2b104
Revises: c4d2e8f1a703
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e3f9a2b104"
down_revision: Union[str, None] = "c4d2e8f1a703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ih_v2_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("review_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ih_weekly_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column("risk", sa.Text(), nullable=True),
        sa.Column("status", sa.String(14), nullable=False, server_default=sa.text("'PROPOSED'")),
        sa.Column("user_note", sa.Text(), nullable=True),
        sa.Column("apply_plan", postgresql.JSONB(), nullable=True),
        sa.Column("challenger_id", sa.String(8), nullable=True, unique=True),
        sa.Column("challenger_mode", sa.String(16), nullable=True),
        sa.Column("params_override", postgresql.JSONB(), nullable=True),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("history", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("review_id", "idx", name="uq_ih_v2_proposals_review_idx"),
    )
    op.create_index("idx_ih_v2_proposals_status", "ih_v2_proposals", ["status"])


def downgrade() -> None:
    op.drop_index("idx_ih_v2_proposals_status", table_name="ih_v2_proposals")
    op.drop_table("ih_v2_proposals")
