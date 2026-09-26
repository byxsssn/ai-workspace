from sqlalchemy import DateTime, String, Text, UniqueConstraint, Uuid

from ai_workspace.db.base import Base
from ai_workspace.models import ProviderCredential, User


def test_provider_credential_metadata_has_required_constraints() -> None:
    table = ProviderCredential.__table__

    assert issubclass(ProviderCredential, Base)
    assert table.metadata is Base.metadata
    assert Base.metadata.tables["provider_credentials"] is table
    assert table.metadata is User.__table__.metadata
    assert set(table.columns.keys()) == {
        "id",
        "user_id",
        "provider",
        "encrypted_api_key",
        "base_url",
        "created_at",
        "updated_at",
    }
    assert all(
        column.nullable is False for column in table.c if column.name != "base_url"
    )
    assert list(table.primary_key.columns.keys()) == ["id"]
    for column in (table.c.id, table.c.user_id):
        assert isinstance(column.type, Uuid)
        assert column.type.as_uuid is True
    assert table.c.id.default is not None
    assert table.c.id.default.is_callable

    assert len(table.c.user_id.foreign_keys) == 1
    foreign_key = next(iter(table.c.user_id.foreign_keys))
    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.ondelete == "CASCADE"
    assert any(
        index.unique is False and list(index.columns.keys()) == ["user_id"]
        for index in table.indexes
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_provider_credentials_user_id_provider"
        and list(constraint.columns.keys()) == ["user_id", "provider"]
        for constraint in table.constraints
    )

    assert isinstance(table.c.provider.type, String)
    assert table.c.provider.type.length == 64
    assert isinstance(table.c.encrypted_api_key.type, Text)
    assert isinstance(table.c.base_url.type, String)
    assert table.c.base_url.type.length == 2048
    assert table.c.base_url.nullable is True
    for column in (table.c.created_at, table.c.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.server_default is not None
    assert table.c.updated_at.onupdate is not None
