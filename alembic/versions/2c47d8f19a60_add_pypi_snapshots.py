"""Persist public PyPI snapshots and a shared refresh lease."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2c47d8f19a60"
down_revision: str | Sequence[str] | None = "8a2f7d10c6b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pypi_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("source_url", sa.String(255), nullable=False),
        sa.Column("catalog_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_pypi_snapshots_source_updated_at", "pypi_snapshots", ["source_updated_at"]
    )
    op.create_table(
        "pypi_refresh_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("last_error", sa.String(32)),
        sa.CheckConstraint("id = 1", name="ck_pypi_refresh_state_singleton"),
    )


def downgrade() -> None:
    op.drop_table("pypi_refresh_state")
    op.drop_index("ix_pypi_snapshots_source_updated_at", table_name="pypi_snapshots")
    op.drop_table("pypi_snapshots")
