"""#30 前半: 削除要求 API の認証・OpenAPI 契約・再認証判定（DB を使わないもの）。"""

import uuid

import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal, _auth_methods
from alter_api.main import create_app
from alter_domain.account_deletion import recently_reauthenticated
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

PATH = "/api/v1/users/me/deletion"


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("method", ["get", "post"])
def test_requires_token(method: str) -> None:
    client = TestClient(create_app(_settings()), raise_server_exceptions=False)
    response = client.request(method, PATH, json={"confirm": True})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("method", ["get", "post"])
def test_mock_mode_does_not_bypass(method: str) -> None:
    app = create_app(_settings())
    app.dependency_overrides[get_principal] = lambda: Principal(
        uuid.uuid4(), "jwks", (("oauth", 10**12),)
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.request(method, PATH, json={"confirm": True})
    assert response.status_code == 503


def test_openapi_matches_preview_contract() -> None:
    schema = create_app(_settings()).openapi()
    path = schema["paths"][PATH]
    assert path["post"]["operationId"] == "request_deletion"
    assert "202" in path["post"]["responses"]
    assert path["get"]["operationId"] == "get_own_deletion"
    headers = {p["name"] for p in path["post"]["parameters"] if p["in"] == "header"}
    assert headers == {"Idempotency-Key"}
    schemas = schema["components"]["schemas"]
    assert set(schemas["DeletionRequest"]["required"]) == {"id", "status", "requested_at"}
    assert schemas["DeletionRequest"]["properties"]["status"]["enum"] == [
        "pending",
        "running",
        "completed",
        "failed",
    ]
    body = schemas["DeletionInput"]
    assert body["additionalProperties"] is False
    assert body["properties"]["confirm"]["const"] is True


NOW = 1_800_000_000


@pytest.mark.parametrize(
    ("methods", "ok"),
    [
        ((("oauth", NOW - 10),), True),
        ((("oauth", NOW - 300),), True),
        ((("oauth", NOW - 301),), False),
        ((("oauth", NOW + 30),), True),
        ((("oauth", NOW + 31),), False),  # 未来すぎる時刻は信用しない
        ((("password", NOW),), False),
        ((("otp", NOW), ("oauth", NOW - 1000)), False),
        ((("otp", NOW), ("oauth", NOW - 5)), True),
        ((), False),
    ],
)
def test_recently_reauthenticated(methods: tuple[tuple[str, int], ...], ok: bool) -> None:
    assert recently_reauthenticated(methods, NOW) is ok


def test_amr_claims_are_parsed_strictly() -> None:
    claims = {
        "amr": [
            {"method": "oauth", "timestamp": 1},
            {"method": "oauth", "timestamp": "2"},
            {"method": "oauth", "timestamp": True},
            {"method": 3, "timestamp": 3},
            "oauth",
        ]
    }
    assert _auth_methods(claims) == (("oauth", 1),)
    assert _auth_methods({"amr": "oauth"}) == ()
    assert _auth_methods({}) == ()
