"""add node_timings_ms jsonb column to job_apps_runs

Revision ID: 6ebfd6cc10b5
Revises: 9e2bd00e2c8f
Create Date: 2026-05-15 12:08:46.530219

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6ebfd6cc10b5"
down_revision: str | Sequence[str] | None = "9e2bd00e2c8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "job_apps_runs",
        sa.Column("node_timings_ms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("job_apps_runs", "node_timings_ms")
