"""skip feedback: reasons on status events; hidden companies and title words in settings

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("status_events", sa.Column("reasons", postgresql.JSONB(), server_default=_EMPTY, nullable=False))
    op.add_column("user_settings",
                  sa.Column("excluded_companies", postgresql.JSONB(), server_default=_EMPTY, nullable=False))
    op.add_column("user_settings",
                  sa.Column("excluded_title_words", postgresql.JSONB(), server_default=_EMPTY, nullable=False))


def downgrade() -> None:
    op.drop_column("user_settings", "excluded_title_words")
    op.drop_column("user_settings", "excluded_companies")
    op.drop_column("status_events", "reasons")
