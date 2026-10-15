"""创建消息表

迁移版本： f1c4ed1d9920
上一版本： e88660aeee30
创建时间： 2026-09-16 21:50:59.213268

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic 使用的迁移版本标识。
revision: str = "f1c4ed1d9920"
down_revision: str | Sequence[str] | None = "e88660aeee30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    # ### Alembic 自动生成命令结束 ###


def downgrade() -> None:
    """回退数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    # ### Alembic 自动生成命令结束 ###
