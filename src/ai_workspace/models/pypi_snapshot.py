from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_workspace.db.base import Base


class PypiSnapshot(Base):
    __tablename__ = "pypi_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    source_url: Mapped[str] = mapped_column(String(255), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class PypiRefreshState(Base):
    """跨工作进程共享的短租约和退避状态；网络等待期间不持有事务。"""

    __tablename__ = "pypi_refresh_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_pypi_refresh_state_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(32), nullable=True)
