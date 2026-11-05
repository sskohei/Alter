"""#7 前半: 共通エラー形式・request_id・ページング・Idempotency-Key・CORS・OpenAPI。"""

import uuid
from typing import Annotated, Any

import pytest
from alter_api.idempotency import idempotency_key
from alter_api.main import create_app
from alter_api.pagination import Page, PageParams, page_params
from alter_domain.settings import Settings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

ORIGIN = "http://localhost:3000"


def _app() -> FastAPI:
    app = create_app(Settings(_env_file=None, cors_origins=ORIGIN))  # type: ignore[call-arg]

    @app.get("/api/v1/_test/items")
    def items(params: Annotated[PageParams, Depends(page_params)]) -> Page[int]:
        return Page(items=list(range(params.limit)), next_cursor=params.cursor)

    @app.post("/api/v1/_test/start")
    def start(key: Annotated[str | None, Depends(idempotency_key)]) -> dict[str, Any]:
        return {"key": key}

    @app.post("/api/v1/_test/echo")
    def echo(payload: dict[str, int]) -> dict[str, int]:
        return payload

    @app.get("/api/v1/_test/boom")
    def boom() -> None:
        raise RuntimeError("secret detail that must not leak")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def _assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    # request_id は UUID で、応答ヘッダーと一致する
    assert str(uuid.UUID(body["error"]["request_id"])) == body["error"]["request_id"]
    assert response.headers["x-request-id"] == body["error"]["request_id"]
    return body["error"]  # type: ignore[no-any-return]


def test_not_found_uses_common_error(client: TestClient) -> None:
    _assert_error(client.get("/api/v1/does-not-exist"), 404, "NOT_FOUND")


def test_method_not_allowed_uses_common_error(client: TestClient) -> None:
    _assert_error(client.delete("/health"), 405, "METHOD_NOT_ALLOWED")


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.post("/api/v1/_test/echo", json={"a": "my-private-text"})
    error = _assert_error(response, 422, "VALIDATION_ERROR")
    assert "my-private-text" not in response.text
    assert error["message"] == "Request validation failed"


def test_unexpected_error_is_500_without_details(client: TestClient) -> None:
    response = client.get("/api/v1/_test/boom")
    _assert_error(response, 500, "INTERNAL_ERROR")
    assert "secret detail" not in response.text


def test_request_id_on_success_and_not_taken_from_client(client: TestClient) -> None:
    supplied = str(uuid.uuid4())
    first = client.get("/health", headers={"X-Request-ID": supplied})
    second = client.get("/health")
    assert first.status_code == 200
    ids = {first.headers["x-request-id"], second.headers["x-request-id"]}
    assert len(ids) == 2
    assert supplied not in ids


# -- ページング ---------------------------------------------------------------


def test_page_defaults(client: TestClient) -> None:
    response = client.get("/api/v1/_test/items")
    assert response.json() == {"items": list(range(20)), "next_cursor": None}


def test_page_limit_and_cursor(client: TestClient) -> None:
    response = client.get("/api/v1/_test/items", params={"limit": 50, "cursor": "opaque"})
    assert len(response.json()["items"]) == 50
    assert response.json()["next_cursor"] == "opaque"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"cursor": ""}])
def test_page_rejects_out_of_range(client: TestClient, params: dict[str, Any]) -> None:
    _assert_error(client.get("/api/v1/_test/items", params=params), 422, "VALIDATION_ERROR")


# -- Idempotency-Key ----------------------------------------------------------


def test_idempotency_key_optional_and_accepted(client: TestClient) -> None:
    assert client.post("/api/v1/_test/start").json() == {"key": None}
    key = str(uuid.uuid4())
    response = client.post("/api/v1/_test/start", headers={"Idempotency-Key": key})
    assert response.json() == {"key": key}


@pytest.mark.parametrize("key", ["short", "has space inside", "x" * 256, "tab\there-1234"])
def test_idempotency_key_format(client: TestClient, key: str) -> None:
    response = client.post("/api/v1/_test/start", headers={"Idempotency-Key": key})
    _assert_error(response, 422, "VALIDATION_ERROR")


# -- CORS ---------------------------------------------------------------------


def test_cors_preflight_allows_write_methods_and_headers(client: TestClient) -> None:
    response = client.options(
        "/api/v1/_test/start",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,idempotency-key,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/_test/start",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_exposes_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": ORIGIN})
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


@pytest.mark.parametrize("origins", ["*", "http://localhost:3000, *"])
def test_wildcard_origin_is_rejected(origins: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=origins)  # type: ignore[call-arg]


@pytest.mark.parametrize("algorithms", ["HS256", "none", "ES256,HS256", ""])
def test_jwt_algorithms_restricted(algorithms: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_jwt_algorithms=algorithms)  # type: ignore[call-arg]


# -- OpenAPI ------------------------------------------------------------------


def test_openapi_has_shared_components() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]
    schemas = schema["components"]["schemas"]
    assert {"ErrorResponse", "ErrorBody"} <= set(schemas)
    assert "INVITATION_REQUIRED" in schemas["ErrorBody"]["properties"]["code"]["enum"]
    assert schema["components"]["securitySchemes"]["SupabaseBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/health"]["get"]["operationId"] == "get_health"
