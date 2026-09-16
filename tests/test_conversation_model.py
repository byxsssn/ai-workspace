from uuid import UUID

from sqlalchemy import DateTime, String, Uuid

from ai_workspace.db.base import Base
from ai_workspace.models import Conversation, User


def test_conversation_is_registered_with_required_columns() -> None:
    table = Conversation.__table__

    assert issubclass(Conversation, Base)
    assert table.name == "conversations"
    assert table.metadata is Base.metadata
    assert table.metadata is User.__table__.metadata
    assert Base.metadata.tables["conversations"] is table
    assert set(table.columns.keys()) == {
        "id",
        "user_id",
        "title",
        "created_at",
        "updated_at",
    }
    assert all(column.nullable is False for column in table.columns)


def test_conversation_id_is_uuid_primary_key_with_generated_default() -> None:
    table = Conversation.__table__
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


def test_conversation_user_id_has_foreign_key_and_non_unique_index() -> None:
    table = Conversation.__table__
    column = table.c.user_id

    assert isinstance(column.type, Uuid)
    assert column.type.as_uuid is True
    assert column.nullable is False
    assert len(column.foreign_keys) == 1

    foreign_key = next(iter(column.foreign_keys))

    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.ondelete == "CASCADE"
    assert any(
        index.unique is False and list(index.columns.keys()) == ["user_id"]
        for index in table.indexes
    )


def test_conversation_title_has_length_and_defaults() -> None:
    column = Conversation.__table__.c.title

    assert isinstance(column.type, String)
    assert column.type.length == 255
    assert column.default is not None
    assert column.default.arg == "New conversation"
    assert column.server_default is not None
    assert str(column.server_default.arg) == "New conversation"


def test_conversation_timestamps_have_timezone_and_automatic_defaults() -> None:
    table = Conversation.__table__

    for column in (table.c.created_at, table.c.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.server_default is not None
        assert str(column.server_default.arg) == "now()"

    assert table.c.created_at.onupdate is None
    assert table.c.updated_at.onupdate is not None
    assert str(table.c.updated_at.onupdate.arg) == "now()"
