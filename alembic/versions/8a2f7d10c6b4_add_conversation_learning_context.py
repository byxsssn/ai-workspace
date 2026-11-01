"""Persist the immutable learning context selected for a conversation."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8a2f7d10c6b4"
down_revision: str | Sequence[str] | None = "47684641df93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable columns leave all existing ordinary conversations unchanged.
    op.add_column("conversations", sa.Column("learning_topic", sa.String(32)))
    op.add_column("conversations", sa.Column("learning_source", sa.String(32)))
    op.create_check_constraint(
        "ck_conversations_learning_context",
        "conversations",
        "(learning_topic IS NULL AND learning_source IS NULL) OR "
        "(learning_topic IS NOT NULL AND learning_source IS NOT NULL "
        "AND learning_topic = 'fastapi' AND learning_source IN ('demo', 'notion'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conversations_learning_context", "conversations", type_="check"
    )
    op.drop_column("conversations", "learning_source")
    op.drop_column("conversations", "learning_topic")
