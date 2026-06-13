"""create morning_brief_runs table

Revision ID: b3d8f1c20a5e
Revises: 9613afa628ef
Create Date: 2026-06-04 09:12:44.580462

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d8f1c20a5e"
down_revision: str | Sequence[str] | None = "9613afa628ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "morning_brief_runs",
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("tasks_today", sa.Integer(), nullable=True),
        sa.Column("tasks_this_week", sa.Integer(), nullable=True),
        sa.Column("tasks_overdue", sa.Integer(), nullable=True),
        sa.Column("tasks_reschedule", sa.Integer(), nullable=True),
        sa.Column("emails_count", sa.Integer(), nullable=True),
        sa.Column("news_stories", sa.Integer(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("duration_s", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("notion_page_id", sa.Text(), nullable=True),
        sa.Column(
            "errored",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("brief_date"),
    )
    op.create_index(
        "morning_brief_runs_ran_at_idx",
        "morning_brief_runs",
        [sa.text("ran_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("morning_brief_runs_ran_at_idx", table_name="morning_brief_runs")
    op.drop_table("morning_brief_runs")
