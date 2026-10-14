"""创建供应商凭据表

迁移版本： 47684641df93
上一版本： f1c4ed1d9920
创建时间： 2026-09-25 17:53:59.274336

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic 使用的迁移版本标识。
revision: str = "47684641df93"
down_revision: str | Sequence[str] | None = "f1c4ed1d9920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """升级数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_provider_credentials_user_id_provider"
        ),
    )
    op.create_index(
        op.f("ix_provider_credentials_user_id"),
        "provider_credentials",
        ["user_id"],
        unique=False,
    )
    # ### Alembic 自动生成命令结束 ###


def downgrade() -> None:
    """回退数据库结构。"""
    # ### 以下命令由 Alembic 自动生成，请按需调整！ ###
    op.drop_index(
        op.f("ix_provider_credentials_user_id"), table_name="provider_credentials"
    )
    op.drop_table("provider_credentials")
    # ### Alembic 自动生成命令结束 ###
