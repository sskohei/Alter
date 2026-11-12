"""#7 残り: 共通認可（``BusinessConnection``）と ``GET/PATCH /users/me`` の DB 統合テスト。

JWT の検証は #43 のテストで確認済みのため ``get_principal`` を差し替える。
Auth 管理 API は ``httpx.MockTransport`` の偽物、招待は共通ドメイン処理で実 DB に登録する。
"""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from alter_api.auth import get_principal
from alter_api.auth.jwt import Principal
from alter_api.main import create_app
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

ME = "/api/v1/users/me"


@dataclass
class FakeAuth:
    """Supabase Auth 管理 API（``GET /auth/v1/admin/users/{id}``）の偽物。"""

    users: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    unavailable: bool = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unavailable:
            return httpx.Response(500)
        uid = request.url.path.rsplit("/", 1)[1]
        if uid not in self.users:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": uid, "identities": self.users[uid]})

    def set(self, sub: uuid.UUID, *identities: tuple[str, str]) -> AuthUser:
        self.users[str(sub)] = [{"provider": p, "id": pid} for p, pid in identities]
        return AuthUser(sub, tuple(AuthIdentity(p, pid) for p, pid in identities))


@dataclass
class Api:
    app: FastAPI
    client: TestClient
    auth: FakeAuth

    def as_user(self, sub: uuid.UUID) -> TestClient:
        self.app.dependency_overrides[get_principal] = lambda: Principal(sub, "jwks")
        return self.client


@pytest.fixture
def api(engine: Engine) -> Iterator[Api]:
    auth = FakeAuth()
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        alter_mode="live",
        database_url=engine.url.render_as_string(hide_password=False),
        supabase_secret_key="test-only",
    )
    with httpx.Client(transport=httpx.MockTransport(auth.handler)) as http:
        app = create_app(settings, http_client=http)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Api(app, client, auth)


@pytest.fixture
def new_user(engine: Engine, created_users: list[uuid.UUID]) -> Any:
    """auth.users に利用者を作る（テスト後に CASCADE で profiles ごと消える）。"""

    def make() -> uuid.UUID:
        sub = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        created_users.append(sub)
        return sub

    return make


@pytest.fixture
def invited(engine: Engine, api: Api, new_user: Any) -> Any:
    """招待済みの利用者を作り、sub を返す。"""

    def make(*identities: tuple[str, str]) -> uuid.UUID:
        sub = new_user()
        user = api.auth.set(sub, *(identities or (("google", f"g-{sub.hex[:8]}"),)))
        with engine.begin() as conn:
            inv.register_invitation(conn, user, operator="test")
        return sub

    return make


def _profiles(engine: Engine, sub: uuid.UUID) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("select * from public.profiles where id = :id"), {"id": sub})
        return [dict(row._mapping) for row in rows]


def _error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


# -- GET /users/me: 作成・取得 ------------------------------------------------


def test_invited_user_gets_profile_created_once(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited(("google", "g-1"), ("github", "1001"))
    client = api.as_user(sub)
    first = client.get(ME)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["id"] == str(sub)
    assert body["display_name"] == "Alter user"
    assert body["web_notifications"] is True
    assert body["discord_dm_notifications"] is False
    assert body["onboarded_at"] is None
    assert first.headers["cache-control"] == "no-store"

    assert client.get(ME).json() == body
    assert len(_profiles(engine, sub)) == 1


def test_concurrent_first_requests_create_one_profile(
    api: Api, engine: Engine, invited: Any
) -> None:
    sub = invited()
    client = api.as_user(sub)
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: client.get(ME).status_code, range(8)))
    assert statuses == [200] * 8
    assert len(_profiles(engine, sub)) == 1


def test_existing_profile_is_returned_unchanged(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.profiles (id, display_name, onboarded_at) "
                "values (:id, 'はなこ', '2026-11-01T00:00:00Z')"
            ),
            {"id": sub},
        )
    body = api.as_user(sub).get(ME).json()
    assert body["display_name"] == "はなこ"
    assert body["onboarded_at"].startswith("2026-11-01")


# -- 共通認可: 拒否 -----------------------------------------------------------


def test_not_invited_is_403_and_no_profile(api: Api, engine: Engine, new_user: Any) -> None:
    sub = new_user()
    api.auth.set(sub, ("google", "g-stranger"))
    client = api.as_user(sub)
    _error(client.get(ME), 403, "INVITATION_REQUIRED")
    _error(client.patch(ME, json={"display_name": "x"}), 403, "INVITATION_REQUIRED")
    assert _profiles(engine, sub) == []


def test_revoked_invitation_is_rejected(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    client = api.as_user(sub)
    assert client.get(ME).status_code == 200
    with engine.begin() as conn:
        invitation_id = conn.execute(
            text("select id from public.invitations where auth_user_id = :sub"), {"sub": sub}
        ).scalar_one()
        inv.revoke_invitation(conn, invitation_id, operator="test")
    _error(client.get(ME), 403, "ACCOUNT_INACTIVE")
    _error(client.patch(ME, json={"display_name": "x"}), 403, "ACCOUNT_INACTIVE")
    assert _profiles(engine, sub)[0]["display_name"] == "Alter user"


@pytest.mark.parametrize("extra", [("github", "999"), ("email", "mail-id")])
def test_unregistered_or_unsupported_identity_is_rejected(
    api: Api, engine: Engine, invited: Any, extra: tuple[str, str]
) -> None:
    sub = invited(("google", "g-1"))
    # 招待後に Auth 側へ未登録・未対応の identity が連携された
    api.auth.set(sub, ("google", "g-1"), extra)
    _error(api.as_user(sub).get(ME), 403, "INVITATION_REQUIRED")
    assert _profiles(engine, sub) == []


def test_deleted_auth_user_is_rejected(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    del api.auth.users[str(sub)]
    _error(api.as_user(sub).get(ME), 403, "INVITATION_REQUIRED")
    assert _profiles(engine, sub) == []


def test_deleted_profile_is_not_revived(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.profiles (id, display_name, deleted_at) "
                "values (:id, 'A', now())"
            ),
            {"id": sub},
        )
    client = api.as_user(sub)
    _error(client.get(ME), 403, "ACCOUNT_INACTIVE")
    _error(client.patch(ME, json={"display_name": "復活"}), 403, "ACCOUNT_INACTIVE")
    [profile] = _profiles(engine, sub)
    assert profile["deleted_at"] is not None
    assert profile["display_name"] == "A"


def test_auth_admin_failure_is_503_and_no_profile(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    api.auth.unavailable = True
    _error(api.as_user(sub).get(ME), 503, "AUTHORIZATION_UNAVAILABLE")
    assert _profiles(engine, sub) == []


# -- PATCH /users/me ----------------------------------------------------------


def test_patch_updates_trimmed_display_name(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    client = api.as_user(sub)
    assert client.get(ME).status_code == 200

    response = client.patch(ME, json={"display_name": "  たろう  "})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "たろう"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(ME).json()["display_name"] == "たろう"

    long_name = "あ" * 80
    assert client.patch(ME, json={"display_name": long_name}).status_code == 200
    assert _profiles(engine, sub)[0]["display_name"] == long_name


def test_patch_before_first_get_creates_profile(api: Api, engine: Engine, invited: Any) -> None:
    sub = invited()
    response = api.as_user(sub).patch(ME, json={"display_name": "はじめ"})
    assert response.status_code == 200, response.text
    [profile] = _profiles(engine, sub)
    assert profile["display_name"] == "はじめ"


@pytest.mark.parametrize("display_name", ["", "   ", "a" * 81])
def test_patch_invalid_keeps_existing_name(
    api: Api, engine: Engine, invited: Any, display_name: str
) -> None:
    sub = invited()
    client = api.as_user(sub)
    assert client.patch(ME, json={"display_name": "もとの名前"}).status_code == 200
    _error(client.patch(ME, json={"display_name": display_name}), 422, "VALIDATION_ERROR")
    assert _profiles(engine, sub)[0]["display_name"] == "もとの名前"
