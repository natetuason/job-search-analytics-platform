"""salary minimum 90k

On 2026-11-05 I lowered my minimum base target to $90,000/yr. Only changes the
value if it is still the original default, so a Settings edit is never overwritten.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("user_settings", "salary_min", server_default=sa.text("90000"))
    op.execute("UPDATE user_settings SET salary_min = 90000 WHERE id = 1 AND salary_min = 100000")


def downgrade() -> None:
    op.alter_column("user_settings", "salary_min", server_default=sa.text("100000"))
    op.execute("UPDATE user_settings SET salary_min = 100000 WHERE id = 1 AND salary_min = 90000")
