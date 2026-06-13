"""add email_received_at to triager_runs and job_apps_runs

Revision ID: 466e2dc6d2aa
Revises: accb90e942e9
Create Date: 2026-05-16 16:31:08.246173

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "466e2dc6d2aa"
down_revision: str | Sequence[str] | None = "accb90e942e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "triager_runs",
        sa.Column("email_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_apps_runs",
        sa.Column("email_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_triager_runs_email_received_at_desc",
        "triager_runs",
        [sa.text("email_received_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_job_apps_runs_email_received_at_desc",
        "job_apps_runs",
        [sa.text("email_received_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_job_apps_runs_email_received_at_desc", table_name="job_apps_runs")
    op.drop_index("idx_triager_runs_email_received_at_desc", table_name="triager_runs")
    op.drop_column("job_apps_runs", "email_received_at")
    op.drop_column("triager_runs", "email_received_at")
