"""创建用户表

迁移版本： e3404c79bd1c
上一版本：
创建时间： 2026-09-15 22:57:51.350554

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic 使用的迁移版本标识。
revision: str = "e3404c79bd1c"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    # ### Alembic 自动生成命令结束 ###


def downgrade() -> None:
    """回退数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    # ### Alembic 自动生成命令结束 ###
