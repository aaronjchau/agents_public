"""create gmail_watch_state and triager_runs tables

Revision ID: cc9852f06dd4
Revises:
Create Date: 2026-05-04 19:29:30.652596

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc9852f06dd4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gmail_watch_state",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("current_history_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_renewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("email"),
    )
    op.create_table(
        "triager_runs",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("primary_label", sa.String(length=32), nullable=False),
        sa.Column(
            "flagged",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=1000), nullable=True),
        sa.Column("sender", sa.String(length=500), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_triager_runs_classified_at",
        "triager_runs",
        ["classified_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_triager_runs_classified_at", table_name="triager_runs")
    op.drop_table("triager_runs")
    op.drop_table("gmail_watch_state")
