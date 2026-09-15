from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, Uuid

from ai_workspace.db.base import Base
from ai_workspace.models import User


def test_user_is_registered_with_required_columns() -> None:
    table = User.__table__

    assert issubclass(User, Base)
    assert table.name == "users"
    assert table.metadata is Base.metadata
    assert Base.metadata.tables["users"] is table
    assert set(table.columns.keys()) == {
        "id",
        "email",
        "password_hash",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert all(column.nullable is False for column in table.columns)


def test_user_id_is_uuid_primary_key_with_generated_default() -> None:
    table = User.__table__
    column = table.c.id

    assert list(table.primary_key.columns.keys()) == ["id"]
    assert isinstance(column.type, Uuid)
    assert column.type.as_uuid is True
    assert column.default is not None
    assert column.default.is_callable

    first_id = column.default.arg(None)
    second_id = column.default.arg(None)

    assert isinstance(first_id, UUID)
    assert first_id.version == 4
    assert isinstance(second_id, UUID)
    assert second_id.version == 4
    assert first_id != second_id


def test_user_email_has_unique_index_and_credentials_are_strings() -> None:
    table = User.__table__

    assert isinstance(table.c.email.type, String)
    assert table.c.email.type.length == 254
    assert any(
        index.unique and list(index.columns.keys()) == ["email"]
        for index in table.indexes
    )
    assert isinstance(table.c.password_hash.type, String)
    assert table.c.password_hash.type.length == 255


def test_user_is_active_defaults_to_true() -> None:
    column = User.__table__.c.is_active

    assert isinstance(column.type, Boolean)
    assert column.default is not None
    assert column.default.arg is True
    assert column.server_default is not None
    assert str(column.server_default.arg) == "true"


def test_user_timestamps_have_timezone_and_automatic_defaults() -> None:
    table = User.__table__

    for column in (table.c.created_at, table.c.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.server_default is not None
        assert str(column.server_default.arg) == "now()"

    assert table.c.created_at.onupdate is None
    assert table.c.updated_at.onupdate is not None
    assert str(table.c.updated_at.onupdate.arg) == "now()"
