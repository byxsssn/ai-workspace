"""${message}

迁移版本： ${up_revision}
上一版本： ${down_revision | comma,n}
创建时间： ${create_date}

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
${imports if imports else ""}

# Alembic 使用的迁移版本标识。
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """升级数据库结构。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回退数据库结构。"""
    ${downgrades if downgrades else "pass"}
