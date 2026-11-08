"""#7: 招待の共通ドメイン処理と運営 CLI の DB 統合テスト（ADR-014、docs/16）。"""

import io
import threading
import uuid
from typing import Any

import pytest
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from alter_domain.cli.invitations import EXIT_FAILED, EXIT_OK, Deps, main
from alter_domain.settings import Settings
from sqlalchemy import Connection, Engine, text


def _user(google: str | None = None, github: str | None = None, *, extra: Any = ()) -> AuthUser:
    identities = []
    if google:
        identities.append(AuthIdentity("google", google))
    if github:
        identities.append(AuthIdentity("github", github))
    return AuthUser(uuid.uuid4(), tuple(identities) + tuple(extra))


def _register(engine: Engine, user: AuthUser, operator: str = "sskohei") -> uuid.UUID:
    with engine.begin() as conn:
        return inv.register_invitation(conn, user, operator=operator)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _audits(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select action, resource_id, metadata, actor_profile_id from public.audit_events "
                "where resource_type = 'invitation' order by created_at, id"
            )
        )
        return [dict(row._mapping) for row in rows]


# -- 登録 ---------------------------------------------------------------------


def test_register_counts_google_and_github_as_one_person(engine: Engine) -> None:
    user = _user("g-1", "1001")
    invitation_id = _register(engine, user)

    assert (
        _scalar(engine, "select status from public.invitations where id = :id", id=invitation_id)
        == "active"
    )
    assert (
        _scalar(
            engine,
            "select count(*) from public.invitation_identities where invitation_id = :id",
            id=invitation_id,
        )
        == 2
    )
    assert _scalar(engine, "select count(*) from public.invitations where status = 'active'") == 1
    [audit] = _audits(engine)
    assert audit["action"] == "invitation.register"
    assert audit["resource_id"] == str(invitation_id)
    assert audit["metadata"] == {"operator": "sskohei", "result": "success"}
    assert audit["actor_profile_id"] is None


def test_register_rejects_same_sub_twice(engine: Engine) -> None:
    user = _user("g-1")
    _register(engine, user)
    with pytest.raises(inv.AlreadyInvited):
        _register(engine, user)


def test_register_identity_taken_rolls_back(engine: Engine) -> None:
    _register(engine, _user("g-shared"))
    second = AuthUser(
        uuid.uuid4(), (AuthIdentity("github", "77"), AuthIdentity("google", "g-shared"))
    )
    with pytest.raises(inv.IdentityTaken):
        _register(engine, second)
    # 2 人目の招待も github identity も残らない
    assert _scalar(engine, "select count(*) from public.invitations") == 1
    assert (
        _scalar(
            engine,
            "select count(*) from public.invitation_identities where provider_user_id = '77'",
        )
        == 0
    )


@pytest.mark.parametrize(
    "user",
    [
        AuthUser(uuid.uuid4(), ()),
        AuthUser(uuid.uuid4(), (AuthIdentity("google", "g"), AuthIdentity("email", "e"))),
    ],
    ids=["no-identity", "email-linked"],
)
def test_register_rejects_unsupported_users(engine: Engine, user: AuthUser) -> None:
    with pytest.raises((inv.NoAllowedIdentity, inv.UnsupportedIdentity)):
        _register(engine, user)
    assert _scalar(engine, "select count(*) from public.invitations") == 0


def test_concurrent_registration_never_exceeds_ten(engine: Engine) -> None:
    attempts = 16
    barrier = threading.Barrier(attempts)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        user = _user(f"g-concurrent-{i}")
        barrier.wait()
        try:
            _register(engine, user)
            outcome = "ok"
        except inv.CapacityReached:
            outcome = "full"
        except Exception as exc:  # pragma: no cover - 失敗時の表示用
            outcome = f"error:{type(exc).__name__}"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(set(results)) == ["full", "ok"], results
    assert results.count("ok") == inv.MAX_ACTIVE_INVITATIONS
    assert _scalar(engine, "select count(*) from public.invitations where status = 'active'") == 10


def test_revoked_invitation_frees_a_seat(engine: Engine) -> None:
    ids = [_register(engine, _user(f"g-seat-{i}")) for i in range(10)]
    with pytest.raises(inv.CapacityReached):
        _register(engine, _user("g-11"))
    with engine.begin() as conn:
        inv.revoke_invitation(conn, ids[0], operator="sskohei")
    _register(engine, _user("g-11"))


# -- identity 追加 --------------------------------------------------------------


def test_add_identity_for_same_sub(engine: Engine) -> None:
    user = _user("g-1")
    invitation_id = _register(engine, user)
    linked = AuthUser(user.id, (AuthIdentity("google", "g-1"), AuthIdentity("github", "2002")))
    with engine.begin() as conn:
        assert inv.add_identities(conn, invitation_id, linked, operator="sskohei") == ("github",)
    with engine.begin() as conn:
        with pytest.raises(inv.NothingToAdd):
            inv.add_identities(conn, invitation_id, linked, operator="sskohei")


@pytest.mark.parametrize("case", ["other-sub", "replace-google", "revoked"])
def test_add_identity_rejections(engine: Engine, case: str) -> None:
    user = _user("g-1")
    invitation_id = _register(engine, user)
    if case == "other-sub":
        target = AuthUser(uuid.uuid4(), (AuthIdentity("github", "3"),))
        error: type[Exception] = inv.IdentityMismatch
    elif case == "replace-google":
        target = AuthUser(user.id, (AuthIdentity("google", "g-other"),))
        error = inv.IdentityMismatch
    else:
        with engine.begin() as conn:
            inv.revoke_invitation(conn, invitation_id, operator="sskohei")
        target = AuthUser(user.id, (AuthIdentity("google", "g-1"), AuthIdentity("github", "3")))
        error = inv.InvitationRevoked
    with engine.begin() as conn:
        with pytest.raises(error):
            inv.add_identities(conn, invitation_id, target, operator="sskohei")
    assert _scalar(engine, "select count(*) from public.invitation_identities") == 1


# -- 失効（AI 停止・監査と同一トランザクション） -----------------------------------------


def _profile_with_active_agent(conn: Connection, user_id: uuid.UUID) -> uuid.UUID:
    conn.execute(text("insert into auth.users (id) values (:id)"), {"id": user_id})
    conn.execute(
        text("insert into public.profiles (id, display_name) values (:id, 'Tester')"),
        {"id": user_id},
    )
    agent_id = uuid.uuid4()
    conn.execute(
        text("insert into public.agents (id, owner_id, name) values (:id, :owner, 'AI')"),
        {"id": agent_id, "owner": user_id},
    )
    conn.execute(
        text(
            "insert into public.agent_profiles (agent_id, version, personality, values_text, "
            "speaking_style, interaction_policy, shareable_info, restrictions, approved_at) "
            "values (:id, 1, 'p', 'v', 's', 'i', 'sh', 'r', now())"
        ),
        {"id": agent_id},
    )
    conn.execute(
        text(
            "update public.agents set approved_profile_version = 1, status = 'active' "
            "where id = :id"
        ),
        {"id": agent_id},
    )
    conn.execute(
        text("insert into public.agent_settings (agent_id, autonomy_enabled) values (:id, true)"),
        {"id": agent_id},
    )
    return agent_id


def _agent_state(engine: Engine, agent_id: uuid.UUID) -> tuple[str, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select a.status, s.autonomy_enabled from public.agents a "
                "join public.agent_settings s on s.agent_id = a.id where a.id = :id"
            ),
            {"id": agent_id},
        ).one()
        return row.status, row.autonomy_enabled


def test_revoke_stops_ai_and_is_idempotent(engine: Engine, created_users: list[uuid.UUID]) -> None:
    user = _user("g-1")
    created_users.append(user.id)
    with engine.begin() as conn:
        agent_id = _profile_with_active_agent(conn, user.id)
    invitation_id = _register(engine, user)

    with engine.begin() as conn:
        first = inv.revoke_invitation(conn, invitation_id, operator="sskohei")
    assert not first.already_revoked
    assert first.paused_agents == 1
    assert _agent_state(engine, agent_id) == ("paused", False)

    with engine.begin() as conn:
        second = inv.revoke_invitation(conn, invitation_id, operator="sskohei")
    assert second.already_revoked
    assert second.revoked_at == first.revoked_at

    actions = [(a["action"], a["metadata"]["result"]) for a in _audits(engine)]
    assert actions == [
        ("invitation.register", "success"),
        ("invitation.revoke", "success"),
        ("invitation.revoke", "noop"),
    ]


def test_revoke_failure_rolls_back_everything(
    engine: Engine, created_users: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user("g-1")
    created_users.append(user.id)
    with engine.begin() as conn:
        agent_id = _profile_with_active_agent(conn, user.id)
    invitation_id = _register(engine, user)

    def boom(conn: Connection, owner_id: uuid.UUID) -> None:
        raise RuntimeError("simulated failure after AI stop")

    monkeypatch.setattr(inv, "cancel_pending_deliveries", boom)
    deps, out = _cli_deps(engine, {})
    with pytest.raises(RuntimeError):
        main(
            ["--env", "local", "--operator", "sskohei", "revoke", str(invitation_id), "--yes"],
            deps=deps,
        )

    # 失効も AI 停止も成功の監査も残らない
    assert (
        _scalar(engine, "select status from public.invitations where id = :id", id=invitation_id)
        == "active"
    )
    assert _agent_state(engine, agent_id) == ("active", True)
    results = [(a["action"], a["metadata"]) for a in _audits(engine)]
    assert results[-1] == (
        "invitation.revoke",
        {"operator": "sskohei", "result": "failure", "code": "unexpected_error"},
    )
    assert ("invitation.revoke", {"operator": "sskohei", "result": "success"}) not in results


# -- API の照合（evaluate_access） ----------------------------------------------


def test_evaluate_access(engine: Engine) -> None:
    user = _user("g-1", "1001")
    invitation_id = _register(engine, user)
    google_only = _user("g-2")
    _register(engine, google_only)

    def decide(sub: uuid.UUID, auth_user: AuthUser | None) -> inv.AccessDecision:
        with engine.connect() as conn:
            return inv.evaluate_access(conn, sub, auth_user)

    allowed = inv.AccessDecision.ALLOWED
    denied = inv.AccessDecision.NOT_INVITED
    assert decide(user.id, user) == allowed
    # 招待済み identity の一部だけで Auth に連携していても可
    assert decide(user.id, AuthUser(user.id, (AuthIdentity("google", "g-1"),))) == allowed
    assert decide(google_only.id, google_only) == allowed
    # 未登録の identity が後から連携された
    assert (
        decide(
            google_only.id,
            AuthUser(
                google_only.id, (AuthIdentity("google", "g-2"), AuthIdentity("github", "999"))
            ),
        )
        == denied
    )
    # 未対応プロバイダーが連携されている
    assert (
        decide(
            google_only.id,
            AuthUser(google_only.id, (AuthIdentity("google", "g-2"), AuthIdentity("email", "x"))),
        )
        == denied
    )
    # identity なし・Auth ユーザーなし・招待なし
    assert decide(user.id, AuthUser(user.id, ())) == denied
    assert decide(user.id, None) == denied
    stranger = _user("g-3")
    assert decide(stranger.id, stranger) == denied
    # 別人の provider ID を名乗っても sub が違えば招待なし
    assert decide(stranger.id, AuthUser(stranger.id, (AuthIdentity("google", "g-1"),))) == denied
    # 失効後は拒否
    with engine.begin() as conn:
        inv.revoke_invitation(conn, invitation_id, operator="sskohei")
    assert decide(user.id, user) == denied
    # sub と Auth 応答の不一致はデータ不整合（503）
    with pytest.raises(inv.InvitationDataError):
        decide(google_only.id, user)


# -- 運営 CLI（対話入力・表示・監査に識別子を残さない） ------------------------------------


def _cli_deps(
    engine: Engine, users: dict[uuid.UUID, AuthUser], stdin: str = ""
) -> tuple[Deps, io.StringIO]:
    out = io.StringIO()
    settings = Settings(_env_file=None, supabase_url="http://127.0.0.1:54321")  # type: ignore[call-arg]
    return (
        Deps(
            settings=settings,
            engine=engine,
            get_auth_user=users.get,
            stdin=io.StringIO(stdin),
            stdout=out,
        ),
        out,
    )


def _cli(
    engine: Engine, users: dict[uuid.UUID, AuthUser], *args: str, stdin: str = ""
) -> tuple[int, str]:
    deps, out = _cli_deps(engine, users, stdin)
    code = main(["--env", "local", "--operator", "sskohei", *args], deps=deps)
    return code, out.getvalue()


def test_cli_end_to_end_without_leaking_identifiers(engine: Engine) -> None:
    user = AuthUser(
        uuid.uuid4(),
        (AuthIdentity("google", "google-secretish-id", display_hint="friend@example.com"),),
    )
    users = {user.id: user}

    # 確認の入力が違えば何も登録しない
    code, _ = _cli(engine, users, "register", stdin=f"{user.id}\nyes\n")
    assert code == EXIT_FAILED
    assert _scalar(engine, "select count(*) from public.invitations") == 0

    code, output = _cli(engine, users, "register", stdin=f"{user.id}\nregister\n")
    assert code == EXIT_OK, output
    invitation_id = uuid.UUID(output.rsplit("招待ID: ", 1)[1].strip())

    code, listing = _cli(engine, users, "list")
    assert code == EXIT_OK
    assert str(invitation_id) in listing
    assert "有効 1 / 10 人" in listing
    assert str(user.id) not in listing
    assert "google-secretish-id" not in listing

    code, detailed = _cli(engine, users, "list", "--show-identifiers")
    assert str(user.id) in detailed and "google-secretish-id" in detailed

    # GitHub を後から連携 → 追加
    users[user.id] = AuthUser(user.id, user.identities + (AuthIdentity("github", "4242"),))
    code, output = _cli(engine, users, "add-identity", str(invitation_id), stdin="add\n")
    assert code == EXIT_OK, output

    code, output = _cli(engine, users, "revoke", str(invitation_id), stdin="revoke\n")
    assert code == EXIT_OK and "失効しました" in output
    code, output = _cli(engine, users, "revoke", str(invitation_id), "--yes")
    assert code == EXIT_OK and "すでに失効済み" in output

    # 失敗も監査される（未登録の招待 ID・Auth にいない sub）
    code, _ = _cli(engine, users, "revoke", str(uuid.uuid4()), "--yes")
    assert code == EXIT_FAILED
    code, _ = _cli(engine, {}, "register", stdin=f"{uuid.uuid4()}\n")
    assert code == EXIT_FAILED

    audits = _audits(engine)
    assert [(a["action"], a["metadata"]["result"]) for a in audits] == [
        ("invitation.register", "success"),
        ("invitation.add_identity", "success"),
        ("invitation.revoke", "success"),
        ("invitation.revoke", "noop"),
        ("invitation.revoke", "failure"),
        ("invitation.register", "failure"),
    ]
    serialized = repr(audits)
    for secret in (str(user.id), "google-secretish-id", "4242", "friend@example.com"):
        assert secret not in serialized
