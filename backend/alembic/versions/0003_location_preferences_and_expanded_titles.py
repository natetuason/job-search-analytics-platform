"""location preferences and expanded titles

Records preferences I stated on 2026-11-04:
- Preferred: New York City, Jersey City, and anywhere in New Jersey.
- Other big U.S. cities are acceptable when the job's match score is >= 35.
- Added supply-chain planning, internal-consulting, and a few secondary titles.

Data updates only touch values that are still at their defaults, so they never
overwrite something already edited in Settings.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREFERRED = ["New York, NY", "Jersey City, NJ", "NJ"]
BIG_CITIES = [
    "Boston, MA", "Philadelphia, PA", "Washington, DC", "Baltimore, MD", "Chicago, IL",
    "Atlanta, GA", "Charlotte, NC", "Miami, FL", "Dallas, TX", "Houston, TX", "Austin, TX",
    "Denver, CO", "Minneapolis, MN", "Seattle, WA", "San Francisco, CA", "San Jose, CA",
    "Los Angeles, CA", "San Diego, CA", "Phoenix, AZ",
]
ADD_PRIMARY = [
    "Supply Chain Planning Analyst", "Demand Planning Analyst", "Supply Planning Analyst",
    "Inventory Analyst", "Inventory Optimization Analyst", "Demand Optimization Analyst",
    "S&OP Analyst", "Procurement Analyst", "Logistics Analyst", "Analytics Analyst",
    "Internal Consultant", "Strategy & Operations Analyst", "Business Transformation Analyst",
]
ADD_SECONDARY = ["Supply Chain Data Scientist", "Associate Product Manager"]


def _append_missing(column: str, titles: list[str]) -> None:
    # jsonb || only the titles not already present, preserving order.
    for title in titles:
        op.execute(sa.text(
            f"UPDATE user_settings SET {column} = {column} || to_jsonb(CAST(:t AS text)) "
            f"WHERE id = 1 AND NOT {column} @> to_jsonb(ARRAY[CAST(:t AS text)])"
        ).bindparams(t=title))


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("secondary_locations", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("user_settings", sa.Column("secondary_location_min_score", sa.Numeric(5, 2), nullable=True))
    op.create_check_constraint(
        op.f("ck_user_settings_secondary_min_score_range"),
        "user_settings",
        "secondary_location_min_score IS NULL OR secondary_location_min_score BETWEEN 0 AND 100",
    )

    op.execute(sa.text(
        "UPDATE user_settings SET preferred_locations = CAST(:p AS jsonb) "
        "WHERE id = 1 AND preferred_locations = '[]'::jsonb"
    ).bindparams(p=json.dumps(PREFERRED)))
    op.execute(sa.text(
        "UPDATE user_settings SET secondary_locations = CAST(:c AS jsonb), secondary_location_min_score = 35 "
        "WHERE id = 1"
    ).bindparams(c=json.dumps(BIG_CITIES)))
    _append_missing("target_titles_primary", ADD_PRIMARY)
    _append_missing("target_titles_secondary", ADD_SECONDARY)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_user_settings_secondary_min_score_range"), "user_settings", type_="check")
    op.drop_column("user_settings", "secondary_location_min_score")
    op.drop_column("user_settings", "secondary_locations")
