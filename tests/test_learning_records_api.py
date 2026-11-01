from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.api.dependencies.learning import get_learning_service
from ai_workspace.api.routes.learning_records import router
from ai_workspace.db.session import get_db_session
from ai_workspace.learning.demo import (
    DEMO_FASTAPI_RECORD_ID,
    demo_learning_record,
    demo_learning_records,
)
from ai_workspace.learning.errors import LearningSourceError
from ai_workspace.learning.service import LearningService
from ai_workspace.models import User


@pytest.fixture
def records_client():
    # 独立应用只挂载本组路由，数据库和外部服务全部替换为测试替身。
    app = FastAPI()
    app.include_router(router)
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = AsyncMock(spec=LearningService)
    service.list_records.return_value = demo_learning_records()
    service.read_record.return_value = demo_learning_record(DEMO_FASTAPI_RECORD_ID)

    async def current_user():
        return user

    async def db_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[get_learning_service] = lambda: service
    with TestClient(app) as client:
        yield client, user, session, service, app


def test_list_defaults_to_notion_and_releases_auth_transaction(records_client):
    client, user, session, service, _ = records_client

    async def list_records(**kwargs):
        session.rollback.assert_awaited_once()
        assert kwargs == {"user_id": user.id, "source": "notion"}
        return demo_learning_records()

    service.list_records.side_effect = list_records
    response = client.get("/learning/records", params={"user_id": str(uuid4())})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["has_more"] is False
    assert payload["items"][0]["id"] == str(DEMO_FASTAPI_RECORD_ID)
    assert payload["items"][0]["topic_id"] == "fastapi"
    assert "notes_markdown" not in payload["items"][0]
    assert "notes_complete" not in payload["items"][0]


def test_explicit_demo_is_forwarded_without_changing_source(records_client):
    client, user, _, service, _ = records_client
    response = client.get("/learning/records?source=demo")
    assert response.status_code == 200 and response.json()["source"] == "demo"
    service.list_records.assert_awaited_once_with(user_id=user.id, source="demo")


@pytest.mark.parametrize("source", ["notion", "demo"])
def test_detail_uses_authenticated_user_and_uuid(records_client, source):
    client, user, session, service, _ = records_client

    async def read_record(**kwargs):
        session.rollback.assert_awaited_once()
        assert kwargs == {
            "user_id": user.id,
            "source": source,
            "record_id": DEMO_FASTAPI_RECORD_ID,
        }
        return demo_learning_record(DEMO_FASTAPI_RECORD_ID)

    service.read_record.side_effect = read_record
    response = client.get(f"/learning/records/{DEMO_FASTAPI_RECORD_ID}?source={source}")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["id"] == str(DEMO_FASTAPI_RECORD_ID)
    assert response.json()["notes_complete"] is True
    assert "虚构" in response.json()["notes_markdown"]


def test_detail_also_defaults_to_notion(records_client):
    client, user, _, service, _ = records_client
    assert client.get(f"/learning/records/{DEMO_FASTAPI_RECORD_ID}").status_code == 200
    service.read_record.assert_awaited_once_with(
        user_id=user.id, source="notion", record_id=DEMO_FASTAPI_RECORD_ID
    )


@pytest.mark.parametrize(
    "path",
    [
        "/learning/records?source=unknown",
        f"/learning/records/{DEMO_FASTAPI_RECORD_ID}?source=unknown",
        "/learning/records/not-a-uuid?source=notion",
    ],
)
def test_invalid_inputs_never_reach_service(records_client, path):
    client, _, _, service, _ = records_client
    assert client.get(path).status_code == 422
    service.list_records.assert_not_called()
    service.read_record.assert_not_called()


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("notion_not_configured", 503),
        ("notion_catalog_not_configured", 503),
        ("notion_not_available", 404),
        ("source_timeout", 504),
        ("source_schema_mismatch", 502),
    ],
)
def test_controlled_list_errors_are_structured_and_not_cached(
    records_client, code, status
):
    client, _, _, service, _ = records_client
    service.list_records.side_effect = LearningSourceError(code, status_code=status)
    response = client.get("/learning/records")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert response.headers["Cache-Control"] == "no-store"


def test_missing_or_out_of_scope_detail_has_one_404_shape(records_client):
    client, _, _, service, _ = records_client
    service.read_record.side_effect = LearningSourceError(
        "learning_record_not_found", status_code=404
    )
    response = client.get(f"/learning/records/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "learning_record_not_found",
        "message": "Learning record not found or unavailable",
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_safe_rate_limit_header_survives_response(records_client):
    client, _, _, service, _ = records_client
    service.list_records.side_effect = LearningSourceError(
        "source_rate_limited", status_code=503, retry_after="5"
    )
    response = client.get("/learning/records")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path", ["/learning/records", f"/learning/records/{DEMO_FASTAPI_RECORD_ID}"]
)
def test_records_endpoints_require_authentication(records_client, path):
    client, _, _, service, app = records_client
    del app.dependency_overrides[get_current_user]
    assert client.get(path).status_code == 401
    service.list_records.assert_not_called()
    service.read_record.assert_not_called()
