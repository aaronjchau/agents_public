"""add stage_timings_ms jsonb column to triager_runs

Revision ID: d5cce57529b8
Revises: accb90e942e9
Create Date: 2026-05-16 16:48:27.709355

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5cce57529b8"
down_revision: str | Sequence[str] | None = "accb90e942e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "triager_runs",
        sa.Column("stage_timings_ms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("triager_runs", "stage_timings_ms")
