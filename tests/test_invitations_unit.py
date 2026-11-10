"""#7: 招待まわりの DB を使わない単体テスト（Auth 管理照会・identity 規則・CLI の安全装置）。"""

import io
import uuid
from typing import Any

import httpx
import pytest
from alter_domain import invitations as inv
from alter_domain.auth_admin import (
    AuthAdminUnavailable,
    AuthIdentity,
    AuthUser,
    SupabaseAuthAdmin,
    parse_auth_user,
)
from alter_domain.cli.invitations import EXIT_FAILED, EXIT_USAGE, Deps, main
from alter_domain.db import create_db_engine
from alter_domain.settings import Settings

SUB = uuid.UUID("6f2b1f0e-3c1a-4b8e-9a4e-1d2c3b4a5f60")


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "supabase_url": "http://127.0.0.1:54321",
        "supabase_secret_key": "test-secret-key",
        "database_url": "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _user_json(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(SUB),
        "email": "user@example.com",
        "user_metadata": {"provider_id": "spoofed"},
        "identities": [
            {
                "identity_id": str(uuid.uuid4()),
                "id": "1234567890",
                "user_id": str(SUB),
                "provider": "google",
                "identity_data": {"email": "user@example.com", "sub": "1234567890"},
            },
            {
                "identity_id": str(uuid.uuid4()),
                "id": "98765",
                "user_id": str(SUB),
                "provider": "github",
                "identity_data": {"user_name": "octo"},
            },
        ],
    }
    data.update(overrides)
    return data


# -- Auth 管理 API --------------------------------------------------------------


def _admin(handler: Any, **settings: Any) -> SupabaseAuthAdmin:
    return SupabaseAuthAdmin(
        _settings(**settings), httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_get_user_parses_provider_ids_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_user_json())

    user = _admin(handler).get_user(SUB)
    assert user == AuthUser(
        id=SUB,
        identities=(AuthIdentity("google", "1234567890"), AuthIdentity("github", "98765")),
    )
    assert str(seen[0].url) == f"http://127.0.0.1:54321/auth/v1/admin/users/{SUB}"
    assert seen[0].headers["apikey"] == "test-secret-key"
    assert seen[0].headers["authorization"] == "Bearer test-secret-key"


def test_get_user_not_found_returns_none() -> None:
    assert _admin(lambda request: httpx.Response(404)).get_user(SUB) is None


@pytest.mark.parametrize("status", [401, 500, 503])
def test_get_user_errors_are_unavailable(status: int) -> None:
    with pytest.raises(AuthAdminUnavailable):
        _admin(lambda request: httpx.Response(status)).get_user(SUB)


def test_get_user_network_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    with pytest.raises(AuthAdminUnavailable):
        _admin(handler).get_user(SUB)


def test_get_user_requires_secret_key() -> None:
    with pytest.raises(AuthAdminUnavailable):
        _admin(
            lambda request: httpx.Response(200, json=_user_json()), supabase_secret_key=""
        ).get_user(SUB)


@pytest.mark.parametrize(
    "data",
    [
        _user_json(id=str(uuid.uuid4())),
        _user_json(identities="nope"),
        _user_json(identities=[{"provider": "google", "id": "", "user_id": str(SUB)}]),
        _user_json(identities=[{"provider": "google", "id": "1", "user_id": str(uuid.uuid4())}]),
        "not-an-object",
    ],
    ids=[
        "other-user",
        "identities-not-list",
        "empty-provider-id",
        "identity-owner-mismatch",
        "bad",
    ],
)
def test_parse_rejects_inconsistent_responses(data: Any) -> None:
    with pytest.raises(AuthAdminUnavailable):
        parse_auth_user(data, expected_id=SUB)


def test_display_hint_is_not_part_of_identity_equality() -> None:
    user = parse_auth_user(_user_json(), expected_id=SUB)
    assert user.identities[0].display_hint == "user@example.com"
    assert user.identities[0] == AuthIdentity("google", "1234567890")


# -- identity の規則 ------------------------------------------------------------


def test_allowed_identities_accepts_google_and_github() -> None:
    user = parse_auth_user(_user_json(), expected_id=SUB)
    assert inv.allowed_identities(user) == user.identities


@pytest.mark.parametrize(
    ("identities", "error"),
    [
        ((), inv.NoAllowedIdentity),
        ((AuthIdentity("google", "1"), AuthIdentity("email", "x")), inv.UnsupportedIdentity),
        ((AuthIdentity("github", "1"), AuthIdentity("github", "2")), inv.UnsupportedIdentity),
    ],
    ids=["none", "email-linked", "two-github"],
)
def test_allowed_identities_rejects(
    identities: tuple[AuthIdentity, ...], error: type[Exception]
) -> None:
    with pytest.raises(error):
        inv.allowed_identities(AuthUser(SUB, identities))


@pytest.mark.parametrize("operator", ["sskohei", "kano.1392", "ops_bot-1"])
def test_operator_ok(operator: str) -> None:
    assert inv.validate_operator(operator) == operator


@pytest.mark.parametrize("operator", ["", "with space", "x" * 41, "名前", "a;drop"])
def test_operator_rejected(operator: str) -> None:
    with pytest.raises(inv.InvalidOperator):
        inv.validate_operator(operator)


# -- CLI の安全装置（DB に接続する前に止まる） ------------------------------------------


def _deps(settings: Settings, stdin: str = "") -> tuple[Deps, io.StringIO]:
    out = io.StringIO()

    def no_auth(sub: uuid.UUID) -> AuthUser | None:
        raise AssertionError("Auth must not be called")

    return (
        Deps(
            settings=settings,
            engine=create_db_engine(settings),  # 接続は遅延するため、ここでは接続しない
            get_auth_user=no_auth,
            stdin=io.StringIO(stdin),
            stdout=out,
        ),
        out,
    )


def test_cli_local_refuses_remote_database() -> None:
    deps, out = _deps(
        _settings(database_url="postgresql+psycopg://u:p@db.example.supabase.co:5432/postgres")
    )
    assert main(["--env", "local", "--operator", "sskohei", "list"], deps=deps) == EXIT_FAILED
    assert "ローカルではありません" in out.getvalue()
    assert "u:p" not in out.getvalue()


def test_cli_production_refuses_local_targets() -> None:
    deps, out = _deps(_settings())
    assert main(["--env", "production", "--operator", "sskohei", "list"], deps=deps) == EXIT_FAILED
    assert "ローカルです" in out.getvalue()


def test_cli_production_requires_typed_confirmation() -> None:
    deps, out = _deps(
        _settings(
            database_url="postgresql+psycopg://u:secret-pass@db.example.supabase.co:5432/postgres",
            supabase_url="https://example.supabase.co",
        ),
        stdin="yes\n",
    )
    assert main(["--env", "production", "--operator", "sskohei", "list"], deps=deps) == EXIT_FAILED
    assert "確認の入力が一致しません" in out.getvalue()
    assert "secret-pass" not in out.getvalue()


def test_cli_rejects_invalid_operator(capsys: pytest.CaptureFixture[str]) -> None:
    deps, _ = _deps(_settings())
    assert main(["--env", "local", "--operator", "bad name", "list"], deps=deps) == EXIT_USAGE


def test_cli_requires_env() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--operator", "sskohei", "list"])
    assert exc.value.code == 2
