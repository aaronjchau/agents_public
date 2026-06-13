"""allow null primary_label on triager_runs

Failure audit rows record a message that errored before classification:
primary_label is NULL and error holds the reason.

Revision ID: 7f3a9c1d2e4b
Revises: b3d8f1c20a5e
Create Date: 2026-06-11 10:26:53.114282

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f3a9c1d2e4b"
down_revision: str | Sequence[str] | None = "b3d8f1c20a5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "triager_runs",
        "primary_label",
        existing_type=sa.String(length=32),
        nullable=True,
    )


def downgrade() -> None:
    # Failure rows violate NOT NULL; they are audit-only, so drop them.
    op.execute("DELETE FROM triager_runs WHERE primary_label IS NULL")
    op.alter_column(
        "triager_runs",
        "primary_label",
        existing_type=sa.String(length=32),
        nullable=False,
    )
