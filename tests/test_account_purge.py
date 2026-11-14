"""#30 後半: Auth・Storage の管理 API クライアントと運営 CLI（DB にも外部にも接続しない）。"""

import json
import uuid

import httpx
import pytest
from alter_api import purge
from alter_domain import account_purge

OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
BASE = "https://supabase.example"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


@pytest.mark.parametrize("status", [200, 204, 404])
def test_auth_delete_accepts_success_and_already_gone(status: int) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status)

    with _client(httpx.MockTransport(handler)) as http:
        account_purge.SupabaseAuthDeleter(BASE, "secret", http).delete_user(OWNER)
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == f"/auth/v1/admin/users/{OWNER}"
    assert seen[0].headers["authorization"] == "Bearer secret"


def test_auth_delete_errors_never_carry_body_or_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret body with user@example.com")

    with _client(httpx.MockTransport(handler)) as http:
        with pytest.raises(account_purge.ExternalUnavailable) as caught:
            account_purge.SupabaseAuthDeleter(BASE, "secret", http).delete_user(OWNER)
    assert caught.value.code == "auth_http_500"
    assert "secret" not in str(caught.value) and "@" not in str(caught.value)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with _client(httpx.MockTransport(broken)) as http:
        with pytest.raises(account_purge.ExternalUnavailable):
            account_purge.SupabaseAuthDeleter(BASE, "secret", http).delete_user(OWNER)
    with _client(httpx.MockTransport(handler)) as http:
        with pytest.raises(account_purge.ExternalUnavailable):
            account_purge.SupabaseAuthDeleter(BASE, "", http).delete_user(OWNER)


def test_storage_purge_lists_and_deletes_owner_prefix_until_empty() -> None:
    files = {f"{OWNER}/a.png", f"{OWNER}/b.png", f"{OWNER}/orphan.bin"}
    calls: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/storage/v1/object/list/agent-icons":
            assert body["prefix"] == f"{OWNER}/"
            names = [{"name": n.split("/", 1)[1], "id": "x"} for n in sorted(files)]
            # 入れ子のフォルダやプレースホルダーは消さない（本人の配下は 1 階層だけ）
            names += [{"name": "nested", "id": None}, {"name": ".emptyFolderPlaceholder"}]
            return httpx.Response(200, json=names if files else [])
        assert request.method == "DELETE" and request.url.path == "/storage/v1/object/agent-icons"
        for name in body["prefixes"]:  # type: ignore[union-attr]
            files.discard(name)
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(handler)) as http:
        removed = account_purge.SupabaseStoragePurger(BASE, "secret", http).purge_prefix(
            "agent-icons", OWNER
        )
    assert removed == 3 and not files
    deleted = [c[2]["prefixes"] for c in calls if c[0] == "DELETE"]
    assert deleted == [[f"{OWNER}/a.png", f"{OWNER}/b.png", f"{OWNER}/orphan.bin"]]


def test_storage_purge_stops_if_deletion_has_no_effect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/list/" in request.url.path:
            return httpx.Response(200, json=[{"name": "stuck.png", "id": "x"}])
        return httpx.Response(200, json=[])

    with _client(httpx.MockTransport(handler)) as http:
        with pytest.raises(account_purge.ExternalUnavailable) as caught:
            account_purge.SupabaseStoragePurger(BASE, "secret", http).purge_prefix(
                "agent-icons", OWNER
            )
    assert caught.value.code == "storage_not_emptied"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(403), "storage_http_403"),
        (httpx.Response(200, text="not json"), "storage_bad_response"),
        (httpx.Response(200, json={"name": "x"}), "storage_bad_response"),
    ],
)
def test_storage_purge_reports_fixed_codes(response: httpx.Response, code: str) -> None:
    with _client(httpx.MockTransport(lambda request: response)) as http:
        with pytest.raises(account_purge.ExternalUnavailable) as caught:
            account_purge.SupabaseStoragePurger(BASE, "secret", http).purge_prefix(
                "profile-private", OWNER
            )
    assert caught.value.code == code


def test_cli_does_nothing_outside_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTER_MODE", "mock")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not touch the database in mock mode")

    monkeypatch.setattr(purge, "create_db_engine", fail)
    assert purge.main([]) == 0
