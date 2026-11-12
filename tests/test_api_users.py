"""#7 残り: ``GET/PATCH /users/me`` の認証・入力検証・OpenAPI（DB を使わないもの）。

招待・停止の判定と作成・更新は ``tests/db/test_users_api_db.py`` で確認する。
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.business import business_connection
from alter_api.main import create_app
from alter_domain.profiles import normalize_display_name
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

SUB = uuid.UUID("6f2b1f0e-3c1a-4b8e-9a4e-1d2c3b4a5f60")
ME = "/api/v1/users/me"


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.mark.parametrize("method", ["get", "patch"])
def test_users_me_requires_token(method: str) -> None:
    client = TestClient(create_app(_settings()), raise_server_exceptions=False)
    # 不正な本文でも、未認証は 422 ではなく 401（入力検証の結果を未認証者に返さない）
    response = client.request(method, ME, json={"bad": 1})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_mock_mode_does_not_bypass_authorization() -> None:
    app = create_app(_settings(alter_mode="mock"))
    app.dependency_overrides[get_principal] = lambda: Principal(SUB, "jwks")
    response = TestClient(app, raise_server_exceptions=False).get(ME)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.fixture
def validation_client() -> TestClient:
    """共通認可を差し替え、DB なしで入力検証だけを確認する。"""

    def no_db() -> Iterator[object]:
        yield object()

    app = create_app(_settings())
    app.dependency_overrides[get_principal] = lambda: Principal(SUB, "jwks")
    app.dependency_overrides[business_connection] = no_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "　\t"},
        {"display_name": "a" * 81},
        {"display_name": "改行\nあり"},
        {"display_name": "nul\u0000"},
        {"display_name": None},
        {"display_name": 123},
        {"display_name": "ok", "web_notifications": False},
    ],
)
def test_patch_rejects_invalid_display_name(validation_client: TestClient, body: Any) -> None:
    response = validation_client.patch(ME, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    # 入力値を応答に反射しない
    assert "改行" not in response.text and "aaaa" not in response.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  たろう  ", "たろう"),
        ("a" * 80, "a" * 80),
        (" " + "あ" * 80 + " ", "あ" * 80),
        ("絵文字🙂", "絵文字🙂"),
    ],
)
def test_normalize_display_name(value: str, expected: str) -> None:
    assert normalize_display_name(value) == expected


def test_openapi_users_me_contract() -> None:
    schema = create_app(_settings()).openapi()
    path = schema["paths"]["/api/v1/users/me"]
    assert path["get"]["operationId"] == "get_current_user"
    assert path["patch"]["operationId"] == "patch_current_user"
    for operation in (path["get"], path["patch"]):
        assert {"401", "403", "404", "409", "422", "429", "503"} <= set(operation["responses"])
        assert operation["security"] == [{"SupabaseBearer": []}]

    schemas = schema["components"]["schemas"]
    assert set(schemas["Profile"]["required"]) == {
        "id",
        "display_name",
        "created_at",
        "web_notifications",
        "discord_dm_notifications",
    }
    assert "onboarded_at" in schemas["Profile"]["properties"]
    patch = schemas["ProfilePatch"]
    assert patch["additionalProperties"] is False
    assert patch["properties"]["display_name"]["maxLength"] == 80
