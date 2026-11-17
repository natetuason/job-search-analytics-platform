"""github list and csv file sources; more target titles

Adds source kinds for public GitHub job lists and CSV files I download myself.
Adds titles I asked for on 2026-11-05: risk analyst, technology analyst, and
product / project management (entry-level gating happens in scoring).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = ("greenhouse", "lever", "google_sheet", "reddit", "manual")
NEW_KINDS = (*OLD_KINDS, "github_list", "csv_file")
TABLES = ("job_sources", "ingestion_runs")

ADD_PRIMARY = ["Risk Analyst", "Technology Analyst", "Project Analyst"]  # Product Analyst is already secondary
ADD_SECONDARY = ["Product Manager", "Project Manager", "Associate Project Manager", "Program Analyst",
                 "Project Coordinator"]


def _set_kinds(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{k}'" for k in kinds)
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_source_kind"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_source_kind"), table, f"source_kind IN ({values})")


def _append_missing(column: str, titles: list[str]) -> None:
    for title in titles:
        op.execute(sa.text(
            f"UPDATE user_settings SET {column} = {column} || to_jsonb(CAST(:t AS text)) "
            f"WHERE id = 1 AND NOT {column} @> to_jsonb(ARRAY[CAST(:t AS text)])"
        ).bindparams(t=title))


def upgrade() -> None:
    _set_kinds(NEW_KINDS)
    _append_missing("target_titles_primary", ADD_PRIMARY)
    _append_missing("target_titles_secondary", ADD_SECONDARY)


def downgrade() -> None:
    for table in TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE source_kind IN ('github_list', 'csv_file')"))
    _set_kinds(OLD_KINDS)
