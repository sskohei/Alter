"""#26: Discord 連携 API の認証・OpenAPI 契約と OAuth クライアント（DB を使わないもの）。"""

import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from alter_api.main import create_app
from alter_domain.discord_link import hash_state
from alter_domain.discord_oauth import (
    DiscordCodeRejected,
    DiscordOAuthClient,
    DiscordOAuthNotConfigured,
    DiscordOAuthUnavailable,
)
from alter_domain.settings import Settings
from fastapi.testclient import TestClient

PATHS = [
    ("get", "/api/v1/discord/connection"),
    ("delete", "/api/v1/discord/connection"),
    ("post", "/api/v1/discord/link/start"),
    ("post", "/api/v1/discord/link/complete"),
    ("get", "/api/v1/discord/guilds"),
]


def _settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(("method", "path"), PATHS)
def test_requires_token(method: str, path: str) -> None:
    client = TestClient(create_app(_settings()), raise_server_exceptions=False)
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_openapi_matches_preview_contract() -> None:
    schema = create_app(_settings()).openapi()
    paths = schema["paths"]
    assert paths["/api/v1/discord/connection"]["get"]["operationId"] == "get_discord_connection"
    assert paths["/api/v1/discord/connection"]["delete"]["operationId"] == "disconnect_discord"
    assert paths["/api/v1/discord/link/start"]["post"]["operationId"] == "start_discord_link"
    complete = paths["/api/v1/discord/link/complete"]["post"]
    assert complete["operationId"] == "complete_discord_link"
    assert "400" in complete["responses"]
    assert paths["/api/v1/discord/guilds"]["get"]["operationId"] == "list_discord_guilds"
    # MVP では Web からギルドの投稿先を設定しない（Bot の /server setup）
    assert not any("channel" in p for p in paths)

    schemas = schema["components"]["schemas"]
    assert set(schemas["DiscordConnection"]["required"]) == {"connected", "discord_user_id"}
    assert set(schemas["DiscordLinkStart"]["required"]) == {"url", "state_expires_at"}
    body = schemas["DiscordLinkComplete"]
    assert body["additionalProperties"] is False
    assert body["properties"]["code"]["maxLength"] == 2048
    assert body["properties"]["state"]["minLength"] == 16
    assert set(schemas["Guild"]["required"]) == {
        "guild_id",
        "channel_id",
        "guild_enabled",
        "consented_at",
    }
    # トークン類は契約に出さない
    text = str(schemas["DiscordConnection"]) + str(schemas["Guild"])
    assert "token" not in text.lower()


def test_state_hash_is_sha256_hex() -> None:
    digest = hash_state("abc")
    assert len(digest) == 64 and digest != "abc"


# -- OAuth クライアント --------------------------------------------------------------------

CONFIG = {
    "discord_client_id": "500000000000000001",
    "discord_client_secret": "secret-value",
    "discord_redirect_uri": "http://web.test/auth/discord/callback",
}


def _client(handler: object, **overrides: str) -> DiscordOAuthClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return DiscordOAuthClient(_settings(**{**CONFIG, **overrides}), http)


def test_authorize_url_asks_identify_only() -> None:
    url = urlparse(_client(lambda r: httpx.Response(500)).authorize_url("s" * 43))
    query = parse_qs(url.query)
    assert url.netloc == "discord.com"
    assert query["scope"] == ["identify"]
    assert query["state"] == ["s" * 43]
    assert "secret-value" not in url.geturl()


@pytest.mark.parametrize("missing", list(CONFIG))
def test_not_configured(missing: str) -> None:
    client = _client(lambda r: httpx.Response(500), **{missing: ""})
    with pytest.raises(DiscordOAuthNotConfigured):
        client.authorize_url("s" * 43)
    with pytest.raises(DiscordOAuthNotConfigured):
        client.fetch_user_id("code")


def _token_ok(scope: str = "identify") -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-access-value", "scope": scope})


@pytest.mark.parametrize(
    ("token", "user", "error"),
    [
        (httpx.Response(400), None, DiscordCodeRejected),
        (httpx.Response(401), None, DiscordCodeRejected),
        (httpx.Response(500), None, DiscordOAuthUnavailable),
        (httpx.Response(200, text="not json"), None, DiscordOAuthUnavailable),
        (_token_ok("guilds"), httpx.Response(500), DiscordOAuthUnavailable),
        (_token_ok(), httpx.Response(401), DiscordOAuthUnavailable),
        (_token_ok(), httpx.Response(200, json={"id": "12"}), DiscordOAuthUnavailable),
        (_token_ok(), httpx.Response(200, json={"id": 10**17}), DiscordOAuthUnavailable),
    ],
)
def test_fetch_user_id_errors(
    token: httpx.Response, user: httpx.Response | None, error: type[Exception]
) -> None:
    revoked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/revoke"):
            revoked.append("x")
            return httpx.Response(200)
        if request.url.path.endswith("/token"):
            return token
        assert user is not None
        if token.json().get("scope") != "identify":
            raise AssertionError("must not call Discord with a token of the wrong scope")
        return user

    with pytest.raises(error) as info:
        _client(handler).fetch_user_id("code")
    assert "fake-access-value" not in str(info.value) and "secret-value" not in str(info.value)
    # トークンを受け取ったときは、失敗しても失効させる
    assert bool(revoked) == (user is not None)


def test_fetch_user_id_success_and_revokes() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200)
        if request.url.path.endswith("/token"):
            assert request.headers["Authorization"].startswith("Basic ")
            return _token_ok()
        assert request.headers["Authorization"] == "Bearer fake-access-value"
        return httpx.Response(200, json={"id": "100000000000000001"})

    assert _client(handler).fetch_user_id("code") == "100000000000000001"
    assert seen == ["/api/v10/oauth2/token", "/api/v10/users/@me", "/api/v10/oauth2/token/revoke"]


def test_network_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(DiscordOAuthUnavailable):
        _client(handler).fetch_user_id(str(uuid.uuid4()))
