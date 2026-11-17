"""adzuna source kind; api call counts on ingestion runs

Adzuna bills per call, so each run records how many requests it sent and the CLI
enforces daily and total caps from those counts.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = ("greenhouse", "lever", "google_sheet", "reddit", "manual", "github_list", "csv_file", "ashby")
NEW_KINDS = (*OLD_KINDS, "adzuna")
TABLES = ("job_sources", "ingestion_runs")


def _set_kinds(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{k}'" for k in kinds)
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_source_kind"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_source_kind"), table, f"source_kind IN ({values})")


def upgrade() -> None:
    _set_kinds(NEW_KINDS)
    op.add_column("ingestion_runs", sa.Column("api_calls", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_runs", "api_calls")
    for table in TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE source_kind = 'adzuna'"))
    _set_kinds(OLD_KINDS)
