"""add token and cost columns to triager_runs and job_apps_runs

Revision ID: 9e2bd00e2c8f
Revises: 368d3ee60f55
Create Date: 2026-05-15 11:54:02.117436

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e2bd00e2c8f"
down_revision: str | Sequence[str] | None = "368d3ee60f55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TOKEN_COST_COLUMNS = (
    ("model", sa.Text()),
    ("input_tokens", sa.Integer()),
    ("output_tokens", sa.Integer()),
    ("cache_read_tokens", sa.Integer()),
    ("cache_creation_5m", sa.Integer()),
    ("cache_creation_1h", sa.Integer()),
    ("cost_usd", sa.Numeric(precision=10, scale=6)),
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in ("triager_runs", "job_apps_runs"):
        for column_name, column_type in _TOKEN_COST_COLUMNS:
            op.add_column(
                table_name,
                sa.Column(column_name, column_type, nullable=True),
            )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in ("triager_runs", "job_apps_runs"):
        for column_name, _ in _TOKEN_COST_COLUMNS:
            op.drop_column(table_name, column_name)
