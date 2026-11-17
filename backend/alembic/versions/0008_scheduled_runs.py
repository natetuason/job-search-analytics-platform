"""scheduled_runs: one row per scheduled update (fetch, close gone listings, rescore) and its digest

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sources_ok", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sources_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("closed_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new_recommended_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("closed_tracked_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name=op.f("ck_scheduled_runs_status")),
        sa.CheckConstraint("trigger IN ('schedule', 'manual')", name=op.f("ck_scheduled_runs_trigger")),
    )
    op.create_index(op.f("ix_scheduled_runs_started_at"), "scheduled_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_runs_started_at"), table_name="scheduled_runs")
    op.drop_table("scheduled_runs")
