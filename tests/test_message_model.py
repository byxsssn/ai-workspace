from uuid import UUID

from sqlalchemy import DateTime, Enum, String, Text, Uuid

from ai_workspace.db.base import Base
from ai_workspace.models import Conversation, Message


def test_message_is_registered_with_required_columns() -> None:
    table = Message.__table__

    assert issubclass(Message, Base)
    assert table.name == "messages"
    assert table.metadata is Base.metadata
    assert table.metadata is Conversation.__table__.metadata
    assert Base.metadata.tables["messages"] is table
    assert set(table.columns.keys()) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "created_at",
    }
    assert all(column.nullable is False for column in table.columns)


def test_message_id_is_uuid_primary_key_with_generated_default() -> None:
    table = Message.__table__
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


def test_message_conversation_id_has_foreign_key_and_non_unique_index() -> None:
    table = Message.__table__
    column = table.c.conversation_id

    assert isinstance(column.type, Uuid)
    assert column.type.as_uuid is True
    assert column.nullable is False
    assert len(column.foreign_keys) == 1

    foreign_key = next(iter(column.foreign_keys))

    assert foreign_key.column is Conversation.__table__.c.id
    assert foreign_key.ondelete == "CASCADE"
    assert any(
        index.unique is False and list(index.columns.keys()) == ["conversation_id"]
        for index in table.indexes
    )


def test_message_role_is_string_and_content_is_text() -> None:
    table = Message.__table__

    assert isinstance(table.c.role.type, String)
    assert not isinstance(table.c.role.type, Enum)
    assert table.c.role.type.length == 32
    assert isinstance(table.c.content.type, Text)


def test_message_created_at_has_timezone_and_automatic_default() -> None:
    column = Message.__table__.c.created_at

    assert isinstance(column.type, DateTime)
    assert column.type.timezone is True
    assert column.server_default is not None
    assert str(column.server_default.arg) == "now()"
    assert column.onupdate is None
