"""#32: API のレート制限（ユーザー・書込・認証失敗の接続元、Retry-After）。"""

import uuid
from typing import Any

import pytest
from alter_api.auth.jwt import Principal, unauthenticated
from alter_api.business import business_connection
from alter_api.main import create_app
from alter_api.rate_limit import RateLimiter, client_key
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

ME = "/api/v1/users/me"
ALICE = uuid.UUID("6f2b1f0e-3c1a-4b8e-9a4e-1d2c3b4a5f60")
BOB = uuid.UUID("7a3c2e1f-4d2b-4c9f-8b5f-2e3d4c5b6a71")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeVerifier:
    """token の文字列で本人を決める（``bad`` は 401）。呼ばれた回数を数える。"""

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, token: str) -> Principal:
        self.calls += 1
        if token == "alice":
            return Principal(ALICE, "jwks")
        if token == "bob":
            return Principal(BOB, "jwks")
        raise unauthenticated()


def _app(clock: Clock, **overrides: Any) -> tuple[FastAPI, FakeVerifier]:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
    app = create_app(settings)
    verifier = FakeVerifier()
    app.state.jwt_verifier = verifier
    app.state.rate_limiter = RateLimiter(clock=clock)

    def no_db() -> Any:
        # 業務の DB 接続まで進んだら 503 で止める（レート制限だけを確かめる）
        from alter_api.errors import ApiError

        raise ApiError(503, "SERVICE_UNAVAILABLE", "no db in this test")
        yield

    app.dependency_overrides[business_connection] = no_db
    return app, verifier


def _client(app: FastAPI, host: str = "127.0.0.1") -> TestClient:
    return TestClient(app, raise_server_exceptions=False, client=(host, 50000))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_user_limit_returns_429_with_retry_after_and_recovers() -> None:
    clock = Clock()
    app, _ = _app(clock, api_rate_limit_per_minute=3)
    client = _client(app)
    for _ in range(3):
        assert client.get(ME, headers=_auth("alice")).status_code == 503
    clock.now += 20
    response = client.get(ME, headers=_auth("alice"))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    # 最初の 1 回が 60 秒の窓から出るまでの秒数
    assert response.headers["Retry-After"] == "40"
    assert response.headers["X-Request-Id"]
    # 別のユーザーは数えない
    assert client.get(ME, headers=_auth("bob")).status_code == 503
    # 窓を過ぎれば戻る
    clock.now += 41
    assert client.get(ME, headers=_auth("alice")).status_code == 503


def test_write_limit_is_separate_from_reads() -> None:
    clock = Clock()
    app, _ = _app(clock, api_write_rate_limit_per_minute=2)
    client = _client(app)
    for _ in range(2):
        assert client.patch(ME, headers=_auth("alice"), json={}).status_code == 503
    response = client.patch(ME, headers=_auth("alice"), json={})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) == 60
    # 読み取りはまだできる
    assert client.get(ME, headers=_auth("alice")).status_code == 503


def test_failed_authentication_is_limited_before_verification() -> None:
    clock = Clock()
    app, verifier = _app(clock, api_unauthenticated_rate_limit_per_minute=2)
    client = _client(app, "203.0.113.7")
    assert client.get(ME).status_code == 401
    assert client.get(ME, headers=_auth("bad")).status_code == 401
    assert verifier.calls == 1
    # 上限に達した接続元は、正しい token でも検証せずに 429
    response = client.get(ME, headers=_auth("alice"))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert verifier.calls == 1
    # 別の接続元は影響を受けない
    assert _client(app, "203.0.113.8").get(ME, headers=_auth("alice")).status_code == 503


def test_successful_requests_do_not_count_as_failures() -> None:
    clock = Clock()
    app, _ = _app(clock, api_unauthenticated_rate_limit_per_minute=1)
    client = _client(app)
    for _ in range(5):
        assert client.get(ME, headers=_auth("alice")).status_code == 503
    assert client.get(ME).status_code == 401


def test_zero_disables_a_limit() -> None:
    clock = Clock()
    app, _ = _app(clock, api_rate_limit_per_minute=0, api_write_rate_limit_per_minute=0)
    client = _client(app)
    for _ in range(80):
        assert client.patch(ME, headers=_auth("alice"), json={}).status_code == 503


def test_health_is_not_limited() -> None:
    clock = Clock()
    app, _ = _app(clock, api_unauthenticated_rate_limit_per_minute=1)
    client = _client(app)
    client.get(ME)
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_cors_preflight_is_not_counted() -> None:
    clock = Clock()
    app, _ = _app(clock, api_unauthenticated_rate_limit_per_minute=1)
    client = _client(app)
    preflight = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    for _ in range(5):
        assert client.options(ME, headers=preflight).status_code == 200
    assert client.get(ME).status_code == 401


def test_429_exposes_retry_after_to_the_web_origin() -> None:
    clock = Clock()
    app, _ = _app(clock, api_rate_limit_per_minute=1)
    client = _client(app)
    headers = {**_auth("alice"), "Origin": "http://localhost:3000"}
    client.get(ME, headers=headers)
    response = client.get(ME, headers=headers)
    assert response.status_code == 429
    assert "Retry-After" in response.headers["Access-Control-Expose-Headers"]


@pytest.mark.parametrize(
    ("peer", "header", "value", "expected"),
    [
        ("127.0.0.1", "CF-Connecting-IP", "198.51.100.4", "198.51.100.4"),
        ("::1", "CF-Connecting-IP", "2001:db8::1", "2001:db8::1"),
        # loopback 以外からのヘッダーは信用しない（中継を通らない直接の接続）
        ("203.0.113.9", "CF-Connecting-IP", "198.51.100.4", "203.0.113.9"),
        # 形式の正しくない値は使わない
        ("127.0.0.1", "CF-Connecting-IP", "evil, 1.2.3.4", "127.0.0.1"),
        # 設定がなければヘッダーを見ない
        ("127.0.0.1", "", "198.51.100.4", "127.0.0.1"),
    ],
)
def test_client_key_trusts_header_only_from_loopback(
    peer: str, header: str, value: str, expected: str
) -> None:
    clock = Clock()
    app, _ = _app(clock, api_client_ip_header=header)
    seen: list[str] = []

    from fastapi import Request

    @app.get("/_key")
    def key(request: Request) -> dict[str, str]:
        seen.append(client_key(request, header))
        return {}

    _client(app, peer).get("/_key", headers={"CF-Connecting-IP": value})
    assert seen == [expected]


def test_limiter_forgets_oldest_keys_beyond_capacity() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock, max_keys=2)
    assert limiter.hit("a", 1) is None
    assert limiter.hit("b", 1) is None
    assert limiter.hit("c", 1) is None  # a を捨てる
    assert limiter.hit("a", 1) is None
    assert limiter.hit("c", 1) == 60
