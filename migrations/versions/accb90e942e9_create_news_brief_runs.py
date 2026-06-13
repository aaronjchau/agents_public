"""create news_brief_runs table

Revision ID: accb90e942e9
Revises: 6ebfd6cc10b5
Create Date: 2026-05-15 12:13:33.964870

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "accb90e942e9"
down_revision: str | Sequence[str] | None = "6ebfd6cc10b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "news_brief_runs",
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("emails_fetched", sa.Integer(), nullable=False),
        sa.Column("stories_considered", sa.Integer(), nullable=True),
        sa.Column("stories_included", sa.Integer(), nullable=True),
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
        "news_brief_runs_ran_at_idx",
        "news_brief_runs",
        [sa.text("ran_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("news_brief_runs_ran_at_idx", table_name="news_brief_runs")
    op.drop_table("news_brief_runs")
