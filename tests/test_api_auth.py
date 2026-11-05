"""#7 前半: Supabase access token の検証（JWKS / HS256 時の Auth 照会）。

外部の Supabase には接続せず、httpx.MockTransport で JWKS と /auth/v1/user を偽装する。
"""

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from alter_api.auth import CurrentPrincipal
from alter_api.main import create_app
from alter_domain.settings import Settings
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ISSUER = "http://auth.test/auth/v1"
AUDIENCE = "authenticated"
SUB = "6f2b1f0e-3c1a-4b8e-9a4e-1d2c3b4a5f60"
JWKS_URL = "http://auth.test/auth/v1/.well-known/jwks.json"
USER_URL = "http://auth.test/auth/v1/user"


def _ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key: Any, kid: str) -> dict[str, Any]:
    algorithm = ECAlgorithm if isinstance(private_key, ec.EllipticCurvePrivateKey) else RSAAlgorithm
    data: dict[str, Any] = json.loads(algorithm.to_jwk(private_key.public_key()))
    data["kid"] = kid
    return data


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUB,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    for name, value in overrides.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return claims


@dataclass
class FakeSupabase:
    """JWKS と /auth/v1/user の偽物。呼出回数を数える。"""

    jwks: list[dict[str, Any]] = field(default_factory=list)
    jwks_status: int = 200
    jwks_error: bool = False
    user_status: int = 200
    user_body: dict[str, Any] | None = None
    user_error: bool = False
    jwks_calls: int = 0
    user_calls: int = 0
    seen_user_headers: list[httpx.Headers] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == JWKS_URL:
            self.jwks_calls += 1
            if self.jwks_error:
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(self.jwks_status, json={"keys": self.jwks})
        if url == USER_URL:
            self.user_calls += 1
            self.seen_user_headers.append(request.headers)
            if self.user_error:
                raise httpx.ConnectTimeout("timeout", request=request)
            body = self.user_body or {"id": SUB, "aud": AUDIENCE, "role": "authenticated"}
            return httpx.Response(self.user_status, json=body)
        return httpx.Response(404)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "supabase_url": "http://auth.test",
        "supabase_jwt_issuer": ISSUER,
        "supabase_jwt_audience": AUDIENCE,
        "supabase_publishable_key": "test-publishable-key",
        "cors_origins": "http://localhost:3000",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _app(fake: FakeSupabase, **settings: Any) -> FastAPI:
    app = create_app(
        _settings(**settings), http_client=httpx.Client(transport=httpx.MockTransport(fake.handler))
    )

    @app.get("/api/v1/_test/me")
    def me(principal: CurrentPrincipal) -> dict[str, str]:
        return {"sub": str(principal.sub), "verified_by": principal.verified_by}

    return app


@pytest.fixture
def ec_key() -> ec.EllipticCurvePrivateKey:
    return _ec_key()


@pytest.fixture
def fake(ec_key: ec.EllipticCurvePrivateKey) -> FakeSupabase:
    return FakeSupabase(jwks=[_jwk(ec_key, "k1")])


@pytest.fixture
def client(fake: FakeSupabase) -> TestClient:
    return TestClient(_app(fake))


def _sign(key: Any, kid: str | None = "k1", alg: str = "ES256", **claims: Any) -> str:
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(_claims(**claims), key, algorithm=alg, headers=headers)


def _get(client: TestClient, token: str | None, scheme: str = "Bearer") -> httpx.Response:
    headers = {"Authorization": f"{scheme} {token}"} if token is not None else {}
    return client.get("/api/v1/_test/me", headers=headers)


def _assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


# -- 正常系 -------------------------------------------------------------------


def test_valid_es256_token(client: TestClient, ec_key: ec.EllipticCurvePrivateKey) -> None:
    response = _get(client, _sign(ec_key))
    assert response.status_code == 200
    assert response.json() == {"sub": SUB, "verified_by": "jwks"}


def test_valid_rs256_token() -> None:
    key = _rsa_key()
    fake = FakeSupabase(jwks=[_jwk(key, "r1")])
    response = _get(TestClient(_app(fake)), _sign(key, kid="r1", alg="RS256"))
    assert response.status_code == 200


def test_jwks_is_cached(
    client: TestClient, fake: FakeSupabase, ec_key: ec.EllipticCurvePrivateKey
) -> None:
    for _ in range(3):
        assert _get(client, _sign(ec_key)).status_code == 200
    assert fake.jwks_calls == 1


# -- 401: 認証ヘッダー・形式 ---------------------------------------------------


def test_missing_authorization(client: TestClient) -> None:
    _assert_401(_get(client, None))


def test_non_bearer_scheme(client: TestClient, ec_key: ec.EllipticCurvePrivateKey) -> None:
    _assert_401(_get(client, _sign(ec_key), scheme="Basic"))


@pytest.mark.parametrize("token", ["garbage", "a.b.c", "x" * 9000])
def test_malformed_token(client: TestClient, token: str) -> None:
    _assert_401(_get(client, token))


# -- 401: 署名・アルゴリズム ---------------------------------------------------


def test_wrong_signature(client: TestClient) -> None:
    _assert_401(_get(client, _sign(_ec_key())))


def test_alg_none_rejected(client: TestClient) -> None:
    token = jwt.encode(_claims(), key=None, algorithm="none", headers={"kid": "k1"})
    _assert_401(_get(client, token))


def test_missing_kid_rejected(client: TestClient, ec_key: ec.EllipticCurvePrivateKey) -> None:
    _assert_401(_get(client, _sign(ec_key, kid=None)))


def test_alg_must_match_key_type(ec_key: ec.EllipticCurvePrivateKey) -> None:
    rsa_key = _rsa_key()
    fake = FakeSupabase(jwks=[_jwk(ec_key, "k1"), _jwk(rsa_key, "r1")])
    client = TestClient(_app(fake))
    # RS256 で署名し、EC 鍵の kid を名乗る
    _assert_401(_get(client, _sign(rsa_key, kid="k1", alg="RS256")))


def test_disallowed_algorithm_by_settings() -> None:
    key = _rsa_key()
    fake = FakeSupabase(jwks=[_jwk(key, "r1")])
    client = TestClient(_app(fake, supabase_jwt_algorithms="ES256"))
    _assert_401(_get(client, _sign(key, kid="r1", alg="RS256")))


def test_hs256_rejected_when_asymmetric_keys_exist(client: TestClient, fake: FakeSupabase) -> None:
    token = jwt.encode(_claims(), "shared-secret-for-test-only-32bytes!!", algorithm="HS256")
    _assert_401(_get(client, token))
    assert fake.user_calls == 0


# -- 401: claim ---------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 3600},
        {"nbf": int(time.time()) + 3600},
        {"iss": "http://evil.test/auth/v1"},
        {"aud": "anon"},
        {"role": "anon"},
        {"role": "service_role"},
        {"sub": "not-a-uuid"},
        {"sub": SUB.upper()},
        {"exp": None},
        {"sub": None},
        {"aud": None},
    ],
    ids=[
        "expired",
        "not-yet-valid",
        "wrong-iss",
        "wrong-aud",
        "anon-role",
        "service-role",
        "sub-not-uuid",
        "sub-not-canonical",
        "missing-exp",
        "missing-sub",
        "missing-aud",
    ],
)
def test_invalid_claims(
    client: TestClient, ec_key: ec.EllipticCurvePrivateKey, overrides: dict[str, Any]
) -> None:
    _assert_401(_get(client, _sign(ec_key, **overrides)))


def test_clock_leeway_is_at_most_30_seconds(
    client: TestClient, ec_key: ec.EllipticCurvePrivateKey
) -> None:
    now = int(time.time())
    assert _get(client, _sign(ec_key, exp=now - 20)).status_code == 200
    _assert_401(_get(client, _sign(ec_key, exp=now - 45)))


# -- 鍵の更新・JWKS 障害 ------------------------------------------------------


def test_unknown_kid_refetches_jwks_once(ec_key: ec.EllipticCurvePrivateKey) -> None:
    clock = _FakeClock()
    fake = FakeSupabase(jwks=[_jwk(ec_key, "k1")])
    client = TestClient(_app_with_clock(fake, clock))
    assert _get(client, _sign(ec_key)).status_code == 200

    # 鍵ローテーション: Supabase 側に k2 が追加された
    new_key = _ec_key()
    fake.jwks = [_jwk(ec_key, "k1"), _jwk(new_key, "k2")]
    clock.advance(60)
    assert _get(client, _sign(new_key, kid="k2")).status_code == 200
    assert fake.jwks_calls == 2


def test_unknown_kid_refetch_is_rate_limited(ec_key: ec.EllipticCurvePrivateKey) -> None:
    clock = _FakeClock()
    fake = FakeSupabase(jwks=[_jwk(ec_key, "k1")])
    client = TestClient(_app_with_clock(fake, clock))
    assert _get(client, _sign(ec_key)).status_code == 200
    # ランダムな kid を連打しても、間隔内は再取得しない
    for i in range(5):
        _assert_401(_get(client, _sign(_ec_key(), kid=f"random-{i}")))
    assert fake.jwks_calls == 1


def test_jwks_outage_returns_503(ec_key: ec.EllipticCurvePrivateKey) -> None:
    fake = FakeSupabase(jwks=[_jwk(ec_key, "k1")], jwks_error=True)
    response = _get(TestClient(_app(fake)), _sign(ec_key))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHORIZATION_UNAVAILABLE"


def test_jwks_server_error_returns_503(ec_key: ec.EllipticCurvePrivateKey) -> None:
    fake = FakeSupabase(jwks=[_jwk(ec_key, "k1")], jwks_status=500)
    assert _get(TestClient(_app(fake)), _sign(ec_key)).status_code == 503


# -- 共有鍵 HS256（JWKS が空）: Auth サーバーで確認 -----------------------------

HS_SECRET = "local-shared-secret-not-known-to-api"


def _hs_token(**claims: Any) -> str:
    return jwt.encode(_claims(**claims), HS_SECRET, algorithm="HS256")


def test_hs256_verified_by_auth_server() -> None:
    fake = FakeSupabase(jwks=[])
    response = _get(TestClient(_app(fake)), _hs_token())
    assert response.status_code == 200
    assert response.json() == {"sub": SUB, "verified_by": "auth_server"}
    assert fake.user_calls == 1
    assert fake.seen_user_headers[0]["apikey"] == "test-publishable-key"


def test_hs256_auth_server_rejects() -> None:
    fake = FakeSupabase(jwks=[], user_status=401)
    _assert_401(_get(TestClient(_app(fake)), _hs_token()))


@pytest.mark.parametrize(
    "body",
    [
        {"id": str(uuid.uuid4()), "aud": AUDIENCE, "role": "authenticated"},
        {"id": SUB, "aud": AUDIENCE, "role": "anon"},
    ],
    ids=["different-user", "not-authenticated"],
)
def test_hs256_auth_server_identity_mismatch(body: dict[str, Any]) -> None:
    fake = FakeSupabase(jwks=[], user_body=body)
    _assert_401(_get(TestClient(_app(fake)), _hs_token()))


@pytest.mark.parametrize("failure", ["status", "network"])
def test_hs256_auth_server_outage_returns_503(failure: str) -> None:
    fake = FakeSupabase(jwks=[], user_status=500 if failure == "status" else 200)
    fake.user_error = failure == "network"
    response = _get(TestClient(_app(fake)), _hs_token())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHORIZATION_UNAVAILABLE"


def test_hs256_expired_token_is_rejected_before_auth_server() -> None:
    fake = FakeSupabase(jwks=[])
    _assert_401(_get(TestClient(_app(fake)), _hs_token(exp=int(time.time()) - 3600)))
    assert fake.user_calls == 0


def test_hs256_wrong_issuer_is_rejected_before_auth_server() -> None:
    fake = FakeSupabase(jwks=[])
    _assert_401(_get(TestClient(_app(fake)), _hs_token(iss="http://evil.test/auth/v1")))
    assert fake.user_calls == 0


# -- トークンをログに出さない ---------------------------------------------------


def test_tokens_are_not_logged(
    caplog: pytest.LogCaptureFixture, ec_key: ec.EllipticCurvePrivateKey
) -> None:
    caplog.set_level(logging.DEBUG)
    tokens = [_sign(ec_key), _sign(_ec_key()), _sign(ec_key, exp=int(time.time()) - 3600)]
    client = TestClient(_app(FakeSupabase(jwks=[_jwk(ec_key, "k1")])))
    for token in tokens:
        _get(client, token)
    outage = TestClient(_app(FakeSupabase(jwks=[], user_error=True)))
    hs = _hs_token()
    tokens.append(hs)
    _get(outage, hs)
    down = TestClient(_app(FakeSupabase(jwks_error=True)))
    _get(down, tokens[0])

    for token in tokens:
        assert token not in caplog.text
        # 署名部分だけでも出していないこと
        assert token.rsplit(".", 1)[1] not in caplog.text
    assert "test-publishable-key" not in caplog.text


# -- helpers ------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _app_with_clock(fake: FakeSupabase, clock: Callable[[], float]) -> FastAPI:
    from alter_api.auth import JwtVerifier

    app = _app(fake)
    app.state.jwt_verifier = JwtVerifier(
        _settings(), httpx.Client(transport=httpx.MockTransport(fake.handler)), clock=clock
    )
    return app
