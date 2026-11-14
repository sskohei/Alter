"""#21: 会話・通知 API の認証と OpenAPI 契約（DB を使わないもの）。"""

import uuid

import pytest
from alter_api.main import create_app
from alter_domain.activity import InvalidCursor, decode_cursor, encode_cursor
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

PATHS = [
    ("get", "/api/v1/conversations"),
    ("get", f"/api/v1/conversations/{uuid.uuid4()}"),
    ("get", "/api/v1/notifications"),
    ("patch", f"/api/v1/notifications/{uuid.uuid4()}/read"),
]


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(("method", "path"), PATHS)
def test_requires_token(method: str, path: str) -> None:
    client = TestClient(create_app(_settings()), raise_server_exceptions=False)
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_openapi_matches_preview_contract() -> None:
    schema = create_app(_settings()).openapi()
    paths = schema["paths"]
    assert paths["/api/v1/conversations"]["get"]["operationId"] == "list_conversations"
    assert paths["/api/v1/conversations/{id}"]["get"]["operationId"] == "get_conversation"
    assert paths["/api/v1/notifications"]["get"]["operationId"] == "list_notifications"
    assert paths["/api/v1/notifications/{id}/read"]["patch"]["operationId"] == "read_notification"
    params = {p["name"] for p in paths["/api/v1/notifications"]["get"]["parameters"]}
    assert params == {"limit", "cursor", "unread"}

    schemas = schema["components"]["schemas"]
    assert set(schemas["ConversationSummary"]["required"]) == {
        "id",
        "status",
        "partner",
        "started_at",
        "summary",
        "visibility",
    }
    cost = schemas["ConversationSummary"]["properties"]["cost_usd"]
    assert "本人のAI" in cost["description"]
    assert {"participants", "messages"} <= set(schemas["ConversationDetail"]["required"])
    assert set(schemas["Notification"]["required"]) == {
        "id",
        "kind",
        "body",
        "created_at",
        "read_at",
    }


def test_cursor_round_trip_and_rejects_garbage() -> None:
    from datetime import UTC, datetime

    created = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    row_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(created, row_id)) == (created, row_id)
    for bad in ("", "!!!", "bm8tc2VwYXJhdG9y", "MjAyNi0xMS0xM3wxMjM"):
        with pytest.raises(InvalidCursor):
            decode_cursor(bad)
