"""#32: 本文の大きさの上限と、入力スキーマの長さの上限（OpenAPI の横断確認）。"""

from collections.abc import Iterator
from typing import Any

import pytest
from alter_api.body_limit import DEFAULT_MAX_BYTES, ICON_HEADROOM_BYTES
from alter_api.main import create_app
from alter_domain.icons import MAX_BYTES as ICON_MAX_BYTES
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

ME = "/api/v1/users/me"
ICONS = "/api/v1/agent-icons"
ORIGIN = "http://localhost:3000"


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    return TestClient(app, raise_server_exceptions=False)


def _assert_too_large(response: Any) -> None:
    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "xxxx" not in response.text  # 本文を反射しない


def test_declared_oversized_body_is_rejected_before_routing(client: TestClient) -> None:
    body = b'{"display_name":"' + b"x" * DEFAULT_MAX_BYTES + b'"}'
    response = client.patch(
        ME, content=body, headers={"Content-Type": "application/json", "Origin": ORIGIN}
    )
    _assert_too_large(response)
    # Web から読めるように CORS ヘッダーが付く
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_streamed_oversized_body_is_cut_off(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"display_name":"'
        for _ in range(DEFAULT_MAX_BYTES // 4096 + 2):
            yield b"x" * 4096
        yield b'"}'

    # Content-Length のない chunked の本文
    response = client.patch(ME, content=chunks(), headers={"Content-Type": "application/json"})
    _assert_too_large(response)


def test_bodies_within_limit_reach_the_route(client: TestClient) -> None:
    # 認証の前で止まらず、ルーターまで届く（未認証なので 401）
    body = b'{"display_name":"' + b"x" * (DEFAULT_MAX_BYTES - 100) + b'"}'
    response = client.patch(ME, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 401


def test_icon_upload_has_its_own_limit(client: TestClient) -> None:
    headers = {"Content-Type": "image/png"}
    # 既定の上限を超えても、画像の上限を少し超える分まではルーターに届く（未認証なので 401。
    # 認証済みなら画像の検証が 422 を返す。tests/db/test_agent_api_db.py）
    for size in (DEFAULT_MAX_BYTES + 1, ICON_MAX_BYTES + 1):
        assert client.post(ICONS, content=b"\0" * size, headers=headers).status_code == 401
    too_big = ICON_MAX_BYTES + ICON_HEADROOM_BYTES + 1
    _assert_too_large(client.post(ICONS, content=b"\0" * too_big, headers=headers))


def test_requests_without_body_are_not_affected(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get(ME).status_code == 401


# -- 入力スキーマの横断確認 -------------------------------------------------------------------

# 自由なキーを受け取ってよい項目（キーはコードで許可リストと照合している）
ALLOWED_FREE_OBJECTS = {"POST /api/v1/agents/{agent_id}/profile/suggest .answers"}


def _schema() -> dict[str, Any]:
    return create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]


def _json_bodies(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                yield f"{method.upper()} {path}", content["application/json"]["schema"]


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = schema["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


def _worst_case_bytes(schema: dict[str, Any], node: dict[str, Any]) -> int:
    """全文字を \\uXXXX でエスケープした場合の、正規の本文の最大の大きさ。"""
    node = _resolve(schema, node)
    for key in ("anyOf", "oneOf"):
        if key in node:
            return max(_worst_case_bytes(schema, n) for n in node[key])
    kind = node.get("type")
    if kind == "string" or "const" in node:
        if "const" in node:
            return len(str(node["const"])) + 2
        if "enum" in node:
            return max(len(str(e)) for e in node["enum"]) + 2
        if node.get("format") in {"uuid", "date-time"}:
            return 40
        return node["maxLength"] * 6 + 2
    if kind == "array":
        return 2 + node["maxItems"] * (_worst_case_bytes(schema, node["items"]) + 1)
    if kind in {"integer", "number"}:
        return 32
    if kind in {"boolean", "null"}:
        return 5
    if kind == "object" or "properties" in node:
        total = 2 + sum(
            len(key) + 4 + _worst_case_bytes(schema, value)
            for key, value in node.get("properties", {}).items()
        )
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            # 自由なキーは許可リスト（人格の項目、最大 10 個）に限られる
            total += 10 * (40 + _worst_case_bytes(schema, extra))
        return total
    return 0


def _unbounded(schema: dict[str, Any], node: dict[str, Any], where: str) -> Iterator[str]:
    node = _resolve(schema, node)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key, []):
            yield from _unbounded(schema, sub, where)
    kind = node.get("type")
    if kind == "string" and not (
        "maxLength" in node
        or "enum" in node
        or "const" in node
        or node.get("format") in {"uuid", "date-time"}
    ):
        yield f"{where}: 文字列に maxLength がない"
    if kind == "array":
        if "maxItems" not in node:
            yield f"{where}: 配列に maxItems がない"
        yield from _unbounded(schema, node.get("items", {}), where + "[]")
    for key, value in node.get("properties", {}).items():
        yield from _unbounded(schema, value, f"{where}.{key}")
    extra = node.get("additionalProperties")
    if isinstance(extra, dict):
        if where not in ALLOWED_FREE_OBJECTS:
            yield f"{where}: 自由なキーを受け取る"
        yield from _unbounded(schema, extra, where + "{}")


def test_every_json_input_is_bounded() -> None:
    schema = _schema()
    problems = [
        problem
        for name, body in _json_bodies(schema)
        for problem in _unbounded(schema, body, name + " ")
    ]
    assert problems == []


def test_largest_legitimate_body_fits_under_the_limit() -> None:
    schema = _schema()
    sizes = {name: _worst_case_bytes(schema, body) for name, body in _json_bodies(schema)}
    assert max(sizes.values()) < DEFAULT_MAX_BYTES, sizes
    # 確認が効いていること（AI の作成が最大で、数十 KB はある）
    assert sizes["POST /api/v1/agents"] > 50_000
