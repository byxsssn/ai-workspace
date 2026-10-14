"""创建会话表

迁移版本： e88660aeee30
上一版本： e3404c79bd1c
创建时间： 2026-09-16 19:52:25.561528

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic 使用的迁移版本标识。
revision: str = "e88660aeee30"
down_revision: str | Sequence[str] | None = "e3404c79bd1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            server_default="New conversation",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False
    )
    # ### Alembic 自动生成命令结束 ###


def downgrade() -> None:
    """回退数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_table("conversations")
    # ### Alembic 自动生成命令结束 ###
