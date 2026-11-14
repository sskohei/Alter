"""#27: Discord Bot コマンドの共通ドメイン処理の DB 統合テスト（Discord には接続しない）。"""

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest
from alter_domain import discord_commands as cmd
from alter_domain import discord_link
from alter_domain import invitations as inv
from alter_domain.auth_admin import AuthIdentity, AuthUser
from sqlalchemy import Connection, Engine, text

WEB = "https://alter.example"
GUILD = cmd.GuildContext("200000000000000001", "テスト鯖")
OTHER_GUILD = cmd.GuildContext("200000000000000002", "別の鯖")
CHANNEL = "300000000000000001"
CHANNEL_2 = "300000000000000002"
OTHER_CHANNEL = "300000000000000009"
ADMIN = cmd.Member("400000000000000001")
OK_TARGET = cmd.SetupTarget(CHANNEL, "ai交流", invoker_can_manage=True, bot_can_post=True)


@dataclass
class Person:
    profile: uuid.UUID
    agent: uuid.UUID
    member: cmd.Member


@pytest.fixture
def people(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[list[Person]]:
    made: list[Person] = []
    yield made
    with engine.begin() as conn:
        conn.execute(
            text(
                "delete from public.conversations where id in ("
                "  select conversation_id from public.conversation_participants "
                "  where agent_id = any(:a))"
            ),
            {"a": [p.agent for p in made]},
        )
        for g in (GUILD, OTHER_GUILD):
            conn.execute(
                text("delete from public.discord_channels where guild_id = :g"), {"g": g.guild_id}
            )
            conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": g.guild_id})
        conn.execute(text("delete from public.audit_events where action like 'discord.%'"))


def _person(engine: Engine, users: list[uuid.UUID], people: list[Person], n: int) -> Person:
    sub = uuid.uuid4()
    discord_id = f"10000000000000000{n}"
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(
            conn, AuthUser(sub, (AuthIdentity("google", f"g-{sub.hex[:10]}"),)), operator="t"
        )
        conn.execute(
            text("insert into public.profiles (id, display_name) values (:id, '本名')"), {"id": sub}
        )
        agent = uuid.uuid4()
        conn.execute(
            text("insert into public.agents (id, owner_id, name) values (:id, :o, :n)"),
            {"id": agent, "o": sub, "n": f"AI{n}"},
        )
        conn.execute(
            text(
                "insert into public.agent_profiles (agent_id, version, personality, values_text, "
                "interests, hobbies, speaking_style, interaction_policy, shareable_info, "
                "restrictions, private_info_ciphertext, private_info_key_version, approved_at) "
                "values (:a, 1, '明るい', '秘密の価値観', '{本,旅}', '{料理}', '丁寧', '方針X', "
                "'大阪在住', '制約Y', decode('00', 'hex'), 1, now())"
            ),
            {"a": agent},
        )
        conn.execute(
            text(
                "update public.agents set status = 'active', approved_profile_version = 1 "
                "where id = :a"
            ),
            {"a": agent},
        )
        conn.execute(
            text(
                "insert into public.agent_settings (agent_id, autonomy_enabled, daily_budget_usd) "
                "values (:a, true, 0.50)"
            ),
            {"a": agent},
        )
        conn.execute(
            text(
                "insert into public.discord_connections (profile_id, discord_user_id) "
                "values (:p, :d)"
            ),
            {"p": sub, "d": discord_id},
        )
    users.append(sub)
    person = Person(sub, agent, cmd.Member(discord_id))
    people.append(person)
    return person


def run(engine: Engine, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        return fn(conn, *args, **kwargs)


def _conversation(
    conn: Connection, a: uuid.UUID, b: uuid.UUID, status: str = "completed"
) -> uuid.UUID:
    cid = conn.execute(
        text("insert into public.conversations (status) values (:s) returning id"), {"s": status}
    ).scalar_one()
    for ordinal, agent in ((1, a), (2, b)):
        conn.execute(
            text(
                "insert into public.conversation_participants "
                "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                "values (:c, :a, :o, true, true)"
            ),
            {"c": cid, "a": agent, "o": ordinal},
        )
    conn.execute(
        text(
            "insert into public.messages (conversation_id, agent_id, turn_no, content, "
            "moderation_status, token_input, token_output) "
            "values (:c, :a, 1, '本文は出さない', 'allowed', 1, 1)"
        ),
        {"c": cid, "a": a},
    )
    return uuid.UUID(str(cid))


@pytest.fixture
def pair(
    engine: Engine, created_users: list[uuid.UUID], people: list[Person]
) -> tuple[Person, Person]:
    return _person(engine, created_users, people, 1), _person(engine, created_users, people, 2)


# -- 本人の解決 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "breakage",
    [
        "update public.discord_connections set revoked_at = now() where profile_id = :p",
        "update public.profiles set deleted_at = now() where id = :p",
        "update public.invitations set status = 'revoked', revoked_at = now() "
        "where auth_user_id = :p",
    ],
)
def test_unlinked_or_inactive_users_are_refused(
    engine: Engine, pair: tuple[Person, Person], breakage: str
) -> None:
    me, _ = pair
    with engine.begin() as conn:
        conn.execute(text(breakage), {"p": me.profile})
    for reply in (
        run(engine, cmd.status, me.member, web_url=WEB),
        run(engine, cmd.history, me.member, None, web_url=WEB),
        run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB),
    ):
        assert reply.ephemeral and "連携されていません" in reply.text
        assert f"{WEB}/settings/discord" in reply.text


def test_unknown_discord_user_is_refused(engine: Engine, pair: tuple[Person, Person]) -> None:
    reply = run(engine, cmd.status, cmd.Member("199999999999999999"), web_url=WEB)
    assert "連携されていません" in reply.text


# -- status・profile・history -----------------------------------------------------------------


def test_status_shows_settings_and_todays_budget(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    me, other = pair
    with engine.begin() as conn:
        conv = _conversation(conn, me.agent, other.agent)
        conn.execute(
            text(
                "insert into public.usage_records (agent_id, conversation_id, provider, model, "
                "input_tokens, output_tokens, estimated_usd, actual_usd, kind, idempotency_key) "
                "values (:a, :c, 'anthropic', 'm', 1, 1, 0.12, 0.12, 'charge', :k), "
                "(:a, :c, 'anthropic', 'm', 1, 1, 0.30, null, 'reserve', :k2)"
            ),
            {"a": me.agent, "c": conv, "k": f"t-{uuid.uuid4()}", "k2": f"t-{uuid.uuid4()}"},
        )
    reply = run(engine, cmd.status, me.member, web_url=WEB)
    assert reply.ephemeral
    assert "AI1（稼働中）" in reply.text
    assert "自律交流: ON" in reply.text
    assert "$0.38 / $0.50" in reply.text


def test_profile_shows_only_public_fields_of_own_agent(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    me, _ = pair
    reply = run(engine, cmd.profile, me.member, None, web_url=WEB)
    assert "明るい" in reply.text and "本、旅" in reply.text and "大阪在住" in reply.text
    for secret in ("秘密の価値観", "方針X", "制約Y", "本名"):
        assert secret not in reply.text
    # 他人の AI は出さない
    assert "見つかりません" in run(engine, cmd.profile, me.member, "AI2", web_url=WEB).text


def test_history_lists_links_without_content(engine: Engine, pair: tuple[Person, Person]) -> None:
    me, other = pair
    with engine.begin() as conn:
        convs = [_conversation(conn, me.agent, other.agent) for _ in range(12)]
        old = _conversation(conn, me.agent, other.agent)
        conn.execute(
            text(
                "update public.conversations set created_at = now() - interval '91 days' "
                "where id = :c"
            ),
            {"c": old},
        )
    reply = run(engine, cmd.history, me.member, 50, web_url=WEB)
    assert reply.ephemeral
    assert len(reply.text.splitlines()) == cmd.HISTORY_MAX
    assert "本文は出さない" not in reply.text
    assert f"{WEB}/conversations/{convs[-1]}" in reply.text
    assert str(old) not in reply.text
    assert "AI2（完了）" in reply.text
    # 相手が削除要求したら履歴からも消える
    with engine.begin() as conn:
        conn.execute(
            text("update public.profiles set deleted_at = now() where id = :p"),
            {"p": other.profile},
        )
    assert (
        "まだ交流の履歴はありません" in run(engine, cmd.history, me.member, None, web_url=WEB).text
    )


# -- pause・resume --------------------------------------------------------------------------


def _autonomy(engine: Engine, agent: uuid.UUID) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("select autonomy_enabled from public.agent_settings where agent_id = :a"),
                {"a": agent},
            ).scalar_one()
        )


def test_pause_and_resume_use_web_rules(engine: Engine, pair: tuple[Person, Person]) -> None:
    me, _ = pair
    kwargs = {"web_url": WEB, "max_daily_budget_usd": Decimal("1.00")}
    reply = run(engine, cmd.set_autonomy, me.member, False, consent_version="", **kwargs)
    assert "停止しました" in reply.text and not _autonomy(engine, me.agent)
    # 同意文面の版が未設定なら、Web と同じく再開できない
    reply = run(engine, cmd.set_autonomy, me.member, True, consent_version="", **kwargs)
    assert "再開できません" in reply.text and not _autonomy(engine, me.agent)
    reply = run(engine, cmd.set_autonomy, me.member, True, consent_version="2026-11", **kwargs)
    assert "再開しました" in reply.text and _autonomy(engine, me.agent)
    with engine.connect() as conn:
        actions = conn.execute(
            text(
                "select count(*) from public.audit_events "
                "where actor_profile_id = :p and action like 'agent_settings%'"
            ),
            {"p": me.profile},
        ).scalar_one()
    assert actions >= 2


# -- /server setup -------------------------------------------------------------------------


def _channels(engine: Engine) -> dict[str, bool]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text("select id, enabled from public.discord_channels where guild_id = :g"),
                {"g": GUILD.guild_id},
            ).all()
        )


def test_setup_requires_manage_permission_and_bot_permissions(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    no_manage = cmd.SetupTarget(CHANNEL, "x", invoker_can_manage=False, bot_can_post=True)
    reply = run(engine, cmd.server_setup, ADMIN, GUILD, no_manage)
    assert "管理権限" in reply.text
    no_post = cmd.SetupTarget(CHANNEL, "x", invoker_can_manage=True, bot_can_post=False)
    assert "投稿できません" in run(engine, cmd.server_setup, ADMIN, GUILD, no_post).text
    assert "サーバーの中" in run(engine, cmd.server_setup, ADMIN, None, OK_TARGET).text
    assert _channels(engine) == {}


def test_setup_and_channel_change_revoke_consents(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    me, other = pair
    assert "設定しました" in run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET).text
    assert _channels(engine) == {CHANNEL: True}
    for person in pair:
        assert (
            "同意しました"
            in run(engine, cmd.publish, person.member, GUILD, True, True, web_url=WEB).text
        )
    with engine.begin() as conn:
        conv = _conversation(conn, me.agent, other.agent)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id) "
                "values (:c, :ch)"
            ),
            {"c": conv, "ch": CHANNEL},
        )
    # 同じ投稿先で再実行しても同意は残る
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    with engine.connect() as conn:
        active = conn.execute(
            text(
                "select count(*) from public.agent_discord_consents "
                "where guild_id = :g and revoked_at is null"
            ),
            {"g": GUILD.guild_id},
        ).scalar_one()
    assert active == 2
    # 投稿先を変えたら、未投稿を取り消して同意も取り消す（再同意まで配送しない）
    moved = cmd.SetupTarget(CHANNEL_2, "新しい場所", invoker_can_manage=True, bot_can_post=True)
    assert (
        "これまでの公開同意は取り消した" in run(engine, cmd.server_setup, ADMIN, GUILD, moved).text
    )
    assert _channels(engine) == {CHANNEL: False, CHANNEL_2: True}
    with engine.connect() as conn:
        active = conn.execute(
            text(
                "select count(*) from public.agent_discord_consents "
                "where guild_id = :g and revoked_at is null"
            ),
            {"g": GUILD.guild_id},
        ).scalar_one()
        status = conn.execute(
            text("select status from public.discord_deliveries where conversation_id = :c"),
            {"c": conv},
        ).scalar_one()
    assert active == 0 and status == "cancelled"


# -- /agent publish ------------------------------------------------------------------------


def _consent(engine: Engine, agent: uuid.UUID, guild: cmd.GuildContext) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select revoked_at is null as active from public.agent_discord_consents "
                "where agent_id = :a and guild_id = :g"
            ),
            {"a": agent, "g": guild.guild_id},
        ).first()
    return "none" if row is None else ("on" if row.active else "off")


def test_publish_needs_guild_setup_and_confirmation(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    me, _ = pair
    assert "サーバーの中" in run(engine, cmd.publish, me.member, None, True, True, web_url=WEB).text
    assert (
        "/server setup" in run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB).text
    )
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    explain = run(engine, cmd.publish, me.member, GUILD, True, False, web_url=WEB)
    assert "完全には回収できません" in explain.text and "#ai交流" in explain.text
    assert _consent(engine, me.agent, GUILD) == "none"
    reply = run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB)
    assert "同意しました" in reply.text
    # 全体設定（discord_publish）が OFF なら、まだ投稿されないことを案内する
    assert "全体設定" in reply.text
    assert _consent(engine, me.agent, GUILD) == "on"
    assert _consent(engine, me.agent, OTHER_GUILD) == "none"  # そのギルドだけ


def test_publish_off_cancels_only_that_guild(engine: Engine, pair: tuple[Person, Person]) -> None:
    me, other = pair
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    other_target = cmd.SetupTarget(OTHER_CHANNEL, "y", invoker_can_manage=True, bot_can_post=True)
    run(engine, cmd.server_setup, ADMIN, OTHER_GUILD, other_target)
    for g in (GUILD, OTHER_GUILD):
        run(engine, cmd.publish, me.member, g, True, True, web_url=WEB)
    with engine.begin() as conn:
        conv = _conversation(conn, me.agent, other.agent)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id) "
                "values (:c, :a), (:c, :b)"
            ),
            {"c": conv, "a": CHANNEL, "b": OTHER_CHANNEL},
        )
    assert (
        "やめました" in run(engine, cmd.publish, me.member, GUILD, False, False, web_url=WEB).text
    )
    assert _consent(engine, me.agent, GUILD) == "off"
    assert _consent(engine, me.agent, OTHER_GUILD) == "on"
    with engine.connect() as conn:
        statuses = dict(
            conn.execute(
                text(
                    "select channel_id, status from public.discord_deliveries "
                    "where conversation_id = :c"
                ),
                {"c": conv},
            ).all()
        )
    assert statuses == {CHANNEL: "cancelled", OTHER_CHANNEL: "pending"}
    # もう一度 on にすれば同意し直せる
    run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB)
    assert _consent(engine, me.agent, GUILD) == "on"


def test_guild_removed_disables_and_cancels(engine: Engine, pair: tuple[Person, Person]) -> None:
    me, other = pair
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    with engine.begin() as conn:
        conv = _conversation(conn, me.agent, other.agent)
        conn.execute(
            text(
                "insert into public.discord_deliveries (conversation_id, channel_id) "
                "values (:c, :ch)"
            ),
            {"c": conv, "ch": CHANNEL},
        )
    run(engine, cmd.guild_removed, GUILD.guild_id)
    with engine.connect() as conn:
        enabled = conn.execute(
            text("select enabled from public.discord_guilds where id = :g"),
            {"g": GUILD.guild_id},
        ).scalar_one()
        status = conn.execute(
            text("select status from public.discord_deliveries where conversation_id = :c"),
            {"c": conv},
        ).scalar_one()
    assert enabled is False and status == "cancelled"
    # 離脱したギルドでは公開に同意できない
    assert (
        "/server setup" in run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB).text
    )


# -- レビュー指摘への回帰テスト（#73） -------------------------------------------------------


def _active_consents(engine: Engine, agent: uuid.UUID) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "select count(*) from public.agent_discord_consents "
                    "where agent_id = :a and revoked_at is null"
                ),
                {"a": agent},
            ).scalar_one()
        )


def test_channel_change_waits_for_in_flight_publish(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    """publish（古い投稿先を確認して同意）の途中で投稿先が変わっても、同意は残らない。"""
    me, _ = pair
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    moved = cmd.SetupTarget(CHANNEL_2, "新しい場所", invoker_can_manage=True, bot_can_post=True)
    done = threading.Event()

    publish_conn = engine.connect()
    tx = publish_conn.begin()
    reply = cmd.publish(publish_conn, me.member, GUILD, True, True, web_url=WEB)
    assert "#ai交流" in reply.text  # まだコミットしていない（古い投稿先への同意）

    def change() -> None:
        run(engine, cmd.server_setup, ADMIN, GUILD, moved)
        done.set()

    worker = threading.Thread(target=change)
    worker.start()
    # 投稿先の変更は、publish のトランザクションが終わるまで待つ
    assert not done.wait(0.5)
    tx.commit()
    publish_conn.close()
    worker.join(5)
    assert done.is_set()
    # 変更が後から実行され、古い投稿先への同意を取り消している
    assert _active_consents(engine, me.agent) == 0


def test_publish_waits_for_channel_change_and_names_new_target(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    me, _ = pair
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    moved = cmd.SetupTarget(CHANNEL_2, "新しい場所", invoker_can_manage=True, bot_can_post=True)
    replies: list[str] = []

    setup_conn = engine.connect()
    tx = setup_conn.begin()
    cmd.server_setup(setup_conn, ADMIN, GUILD, moved)

    def publish() -> None:
        replies.append(run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB).text)

    worker = threading.Thread(target=publish)
    worker.start()
    time.sleep(0.5)
    assert replies == []  # 変更が終わるまで待つ
    tx.commit()
    setup_conn.close()
    worker.join(5)
    # 変更後の投稿先を確認したうえでの同意になり、応答にも新しい投稿先を出す
    assert replies and "#新しい場所" in replies[0]


def test_unlink_during_publish_leaves_no_consent(
    engine: Engine, pair: tuple[Person, Person]
) -> None:
    """連携解除（共通認可と同じロック順）が先に進んでいたら、publish は待ってから拒否する。"""
    me, _ = pair
    run(engine, cmd.server_setup, ADMIN, GUILD, OK_TARGET)
    replies: list[str] = []

    unlink_conn = engine.connect()
    tx = unlink_conn.begin()
    # API の共通認可と同じく、招待 → プロフィールを行ロックしてから解除する
    unlink_conn.execute(
        text("select 1 from public.invitations where auth_user_id = :p for update"),
        {"p": me.profile},
    )
    unlink_conn.execute(
        text("select 1 from public.profiles where id = :p for update"), {"p": me.profile}
    )
    assert discord_link.disconnect(unlink_conn, me.profile)

    def publish() -> None:
        replies.append(run(engine, cmd.publish, me.member, GUILD, True, True, web_url=WEB).text)

    worker = threading.Thread(target=publish)
    worker.start()
    time.sleep(0.5)
    assert replies == []
    tx.commit()
    unlink_conn.close()
    worker.join(5)
    assert replies and "連携されていません" in replies[0]
    # 「連携なし・有効な同意あり」にならない（再連携しても同意は戻らない）
    assert _active_consents(engine, me.agent) == 0


def test_long_profile_fits_discord_limit(engine: Engine, pair: tuple[Person, Person]) -> None:
    me, _ = pair
    long = "あ" * 2000
    tags = ["い" * 50] * 20
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.agent_profiles (agent_id, version, personality, values_text, "
                "interests, hobbies, speaking_style, interaction_policy, shareable_info, "
                "restrictions, approved_at) "
                "values (:a, 2, :t, 'v', :tags, :tags, :t, 'i', :t, 'r', now())"
            ),
            {"a": me.agent, "t": long, "tags": tags},
        )
        conn.execute(
            text("update public.agents set approved_profile_version = 2 where id = :a"),
            {"a": me.agent},
        )
    reply = run(engine, cmd.profile, me.member, None, web_url=WEB)
    assert len(reply.for_discord()) <= cmd.DISCORD_MESSAGE_LIMIT
    assert len(reply.text) <= cmd.DISCORD_MESSAGE_LIMIT
    assert f"{WEB}/agent" in reply.text  # 全体は Web で見られることを案内する
    assert len(cmd.Reply("x" * 5000).for_discord()) == cmd.DISCORD_MESSAGE_LIMIT
