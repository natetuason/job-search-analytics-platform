"""ashby source kind

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = ("greenhouse", "lever", "google_sheet", "reddit", "manual", "github_list", "csv_file")
NEW_KINDS = (*OLD_KINDS, "ashby")
TABLES = ("job_sources", "ingestion_runs")


def _set_kinds(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{k}'" for k in kinds)
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_source_kind"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_source_kind"), table, f"source_kind IN ({values})")


def upgrade() -> None:
    _set_kinds(NEW_KINDS)


def downgrade() -> None:
    for table in TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE source_kind = 'ashby'"))
    _set_kinds(OLD_KINDS)
