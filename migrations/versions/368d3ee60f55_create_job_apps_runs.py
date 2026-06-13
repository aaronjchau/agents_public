"""create job_apps_runs table

Revision ID: 368d3ee60f55
Revises: cc9852f06dd4
Create Date: 2026-05-05 11:42:17.893204

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "368d3ee60f55"
down_revision: str | Sequence[str] | None = "cc9852f06dd4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_apps_runs",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sublabel", sa.String(length=32), nullable=True),
        sa.Column("match_status", sa.String(length=16), nullable=True),
        sa.Column("notion_row_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status_changed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("new_status", sa.String(length=32), nullable=True),
        sa.Column("draft_id", sa.String(length=64), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "errored",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_job_apps_runs_classified_at",
        "job_apps_runs",
        ["classified_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_apps_runs_classified_at", table_name="job_apps_runs")
    op.drop_table("job_apps_runs")
