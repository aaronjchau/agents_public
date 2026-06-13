"""create anthropic_spend table

Revision ID: ea9791aeeccb
Revises: accb90e942e9
Create Date: 2026-05-16 16:23:51.418420

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ea9791aeeccb"
down_revision: str | Sequence[str] | None = "accb90e942e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "anthropic_spend",
        sa.Column("spend_date", sa.Date(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_5m", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_1h", sa.BigInteger(), nullable=True),
        sa.Column(
            "pulled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("spend_date", "model"),
    )
    op.create_index(
        "anthropic_spend_date_idx",
        "anthropic_spend",
        [sa.text("spend_date DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("anthropic_spend_date_idx", table_name="anthropic_spend")
    op.drop_table("anthropic_spend")
