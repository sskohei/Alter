"""#28: 会話投稿・DM の outbox（作成・取り出し・確認し直し・結果の記録・取り消し）。

Discord には接続しない。Bot の送信ループは偽の Discord で最後まで流す。
"""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar

import pytest
from alter_discord_bot import delivery
from alter_domain import discord_delivery as dd
from alter_domain import interaction_settings as settings
from alter_domain import invitations as inv
from alter_domain.activity import notify_conversation_completed
from alter_domain.auth_admin import AuthIdentity, AuthUser
from sqlalchemy import Connection, Engine, text

T = TypeVar("T")
WEB = "https://alter.example"
GUILD = "200000000000000011"
CHANNEL = "300000000000000011"
NEW_CHANNEL = "300000000000000012"


@dataclass
class Person:
    profile: uuid.UUID
    agent: uuid.UUID
    discord_id: str


@pytest.fixture
def world(engine: Engine, created_users: list[uuid.UUID]) -> Iterator[tuple[Person, Person]]:
    people = (_person(engine, created_users, 1), _person(engine, created_users, 2))
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.discord_guilds (id, name, admin_discord_user_id) "
                "values (:g, '鯖', '400000000000000011')"
            ),
            {"g": GUILD},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, 'ai交流', '400000000000000011')"
            ),
            {"c": CHANNEL, "g": GUILD},
        )
        for p in people:
            conn.execute(
                text(
                    "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"
                ),
                {"a": p.agent, "g": GUILD},
            )
    yield people
    with engine.begin() as conn:
        conn.execute(
            text(
                "delete from public.conversations where id in (select conversation_id from "
                "public.conversation_participants where agent_id = any(:a))"
            ),
            {"a": [p.agent for p in people]},
        )
        conn.execute(text("delete from public.discord_channels where guild_id = :g"), {"g": GUILD})
        conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": GUILD})


def _person(engine: Engine, users: list[uuid.UUID], n: int) -> Person:
    sub = uuid.uuid4()
    agent = uuid.uuid4()
    discord_id = f"10000000000000001{n}"
    with engine.begin() as conn:
        conn.execute(text("insert into auth.users (id) values (:id)"), {"id": sub})
        inv.register_invitation(
            conn, AuthUser(sub, (AuthIdentity("google", f"g-{sub.hex[:10]}"),)), operator="t"
        )
        conn.execute(
            text(
                "insert into public.profiles (id, display_name, discord_dm_notifications) "
                "values (:id, '本名', true)"
            ),
            {"id": sub},
        )
        conn.execute(
            text("insert into public.agents (id, owner_id, name) values (:id, :o, :n)"),
            {"id": agent, "o": sub, "n": f"AI{n}"},
        )
        conn.execute(
            text(
                "insert into public.agent_profiles (agent_id, version, personality, values_text, "
                "interests, hobbies, speaking_style, interaction_policy, shareable_info, "
                "restrictions, private_info_ciphertext, private_info_key_version, approved_at) "
                "values (:a, 1, '明るい', '価値観', '{本}', '{料理}', '丁寧', '方針', '共有', "
                "'制約', decode('00', 'hex'), 1, now())"
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
                "insert into public.agent_settings (agent_id, daily_budget_usd, discord_publish) "
                "values (:a, 0.50, true)"
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
    return Person(sub, agent, discord_id)


def _conversation(engine: Engine, a: Person, b: Person, *, consent: bool = True) -> uuid.UUID:
    with engine.begin() as conn:
        cid = uuid.UUID(
            str(
                conn.execute(
                    text(
                        "insert into public.conversations (status, topic, started_at, ended_at) "
                        "values ('completed', '旅の話', now(), now()) returning id"
                    )
                ).scalar_one()
            )
        )
        for ordinal, p in ((1, a), (2, b)):
            conn.execute(
                text(
                    "insert into public.conversation_participants "
                    "(conversation_id, agent_id, ordinal, discord_consent, web_consent) "
                    "values (:c, :a, :o, :d, true)"
                ),
                {"c": cid, "a": p.agent, "o": ordinal, "d": consent},
            )
        for turn, (p, body, mod) in enumerate(
            (
                (a, "こんにちは @everyone", "allowed"),
                (b, "非表示の発言", "blocked"),
                (b, "どうも", "allowed"),
            ),
            start=1,
        ):
            conn.execute(
                text(
                    "insert into public.messages (conversation_id, agent_id, turn_no, content, "
                    "moderation_status) values (:c, :a, :t, :body, :m)"
                ),
                {"c": cid, "a": p.agent, "t": turn, "body": body, "m": mod},
            )
        notify_conversation_completed(conn, cid)
    return cid


def run[R](engine: Engine, fn: Callable[..., R], *args: Any, **kwargs: Any) -> R:
    with engine.begin() as conn:
        return fn(conn, *args, **kwargs)


def _post_rows(engine: Engine, cid: uuid.UUID) -> list[Any]:
    with engine.begin() as conn:
        return list(
            conn.execute(
                text(
                    "select channel_id, status, attempts, last_error, discord_thread_id, "
                    "discord_message_id, next_attempt_at > now() as later "
                    "from public.discord_deliveries where conversation_id = :c"
                ),
                {"c": cid},
            )
        )


def _dm_rows(engine: Engine, cid: uuid.UUID) -> list[Any]:
    with engine.begin() as conn:
        return list(
            conn.execute(
                text(
                    "select d.discord_user_id, d.status, d.last_error from "
                    "public.discord_dm_deliveries d join public.notifications n "
                    "on n.id = d.notification_id where n.conversation_id = :c "
                    "order by d.discord_user_id"
                ),
                {"c": cid},
            )
        )


# -- 作成 -------------------------------------------------------------------------------------


def test_enqueue_creates_one_post_and_dms_and_is_idempotent(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    assert run(engine, dd.enqueue_after_completion, cid) == (1, 2)
    assert run(engine, dd.enqueue_after_completion, cid) == (0, 0)
    rows = _post_rows(engine, cid)
    assert [(r.channel_id, r.status) for r in rows] == [(CHANNEL, "pending")]
    assert [(r.discord_user_id, r.status) for r in _dm_rows(engine, cid)] == [
        (a.discord_id, "pending"),
        (b.discord_id, "pending"),
    ]


POST_BREAKAGES = [
    "update public.agent_settings set discord_publish = false where agent_id = :a",
    "update public.agent_discord_consents set revoked_at = now() where agent_id = :a",
    "update public.discord_connections set revoked_at = now() where profile_id = :p",
    "update public.agents set status = 'paused' where id = :a",
    "update public.profiles set deleted_at = now() where id = :p",
    "update public.discord_channels set enabled = false where id = '300000000000000011'",
    "update public.discord_guilds set enabled = false where id = '200000000000000011'",
    "insert into public.agent_blocks (blocker_agent_id, blocked_agent_id) values (:b, :a)",
    "update public.conversations set created_at = now() - interval '91 days' where id = :c",
    "update public.conversation_participants set discord_consent = false where agent_id = :a",
    "update public.invitations set status = 'revoked', revoked_at = now() where auth_user_id = :p",
]


@pytest.mark.parametrize("breakage", POST_BREAKAGES)
def test_post_requires_every_condition_at_enqueue_and_claim(
    engine: Engine, world: tuple[Person, Person], breakage: str
) -> None:
    a, b = world
    params = {"a": a.agent, "p": a.profile, "b": b.agent}
    # 作成時に満たさなければ作らない
    cid = _conversation(engine, a, b)
    with engine.begin() as conn:
        conn.execute(text(breakage), {**params, "c": cid})
        assert dd.enqueue_conversation_posts(conn, cid) == 0
        conn.rollback()
    # 作成後に条件が崩れたら、取り出し時に取り消す（送らない）
    assert run(engine, dd.enqueue_conversation_posts, cid) == 1
    with engine.begin() as conn:
        conn.execute(text(breakage), {**params, "c": cid})
    assert run(engine, dd.claim_next_post) is None
    assert [(r.status, r.last_error) for r in _post_rows(engine, cid)] == [
        ("cancelled", "not_eligible")
    ]


def test_cancelled_post_is_not_revived(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    with engine.begin() as conn:
        conn.execute(text("update public.discord_deliveries set status = 'cancelled'"))
    assert run(engine, dd.enqueue_conversation_posts, cid) == 0
    assert _post_rows(engine, cid)[0].status == "cancelled"


def test_new_channel_gets_its_own_post(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    with engine.begin() as conn:
        conn.execute(text("update public.discord_channels set enabled = false"))
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, '新', '400000000000000011')"
            ),
            {"c": NEW_CHANNEL, "g": GUILD},
        )
    assert run(engine, dd.enqueue_conversation_posts, cid) == 1
    assert {r.channel_id for r in _post_rows(engine, cid)} == {CHANNEL, NEW_CHANNEL}


OTHER_GUILD = "200000000000000012"
OTHER_CHANNEL = "300000000000000013"


def _second_guild(engine: Engine, people: tuple[Person, ...]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into public.discord_guilds (id, name, admin_discord_user_id) "
                "values (:g, '別鯖', '400000000000000011')"
            ),
            {"g": OTHER_GUILD},
        )
        conn.execute(
            text(
                "insert into public.discord_channels (id, guild_id, name, configured_by) "
                "values (:c, :g, 'ai', '400000000000000011')"
            ),
            {"c": OTHER_CHANNEL, "g": OTHER_GUILD},
        )
        for p in people:
            conn.execute(
                text(
                    "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g)"
                ),
                {"a": p.agent, "g": OTHER_GUILD},
            )


def _drop_second_guild(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("delete from public.discord_deliveries where channel_id = :c"),
            {"c": OTHER_CHANNEL},
        )
        conn.execute(
            text("delete from public.discord_channels where guild_id = :g"), {"g": OTHER_GUILD}
        )
        conn.execute(text("delete from public.discord_guilds where id = :g"), {"g": OTHER_GUILD})


def test_two_eligible_guilds_hold_the_post(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    _second_guild(engine, world)
    try:
        cid = _conversation(engine, a, b)
        assert run(engine, dd.enqueue_conversation_posts, cid) == 2
        # どちらにも送らず、候補を要確認で残す（docs/09: 複数なら保留して管理者が選ぶ）
        assert {(r.channel_id, r.status, r.last_error) for r in _post_rows(engine, cid)} == {
            (CHANNEL, "needs_review", "ambiguous_target"),
            (OTHER_CHANNEL, "needs_review", "ambiguous_target"),
        }
        assert run(engine, dd.claim_next_post) is None
    finally:
        _drop_second_guild(engine)


def test_post_is_held_if_second_guild_becomes_eligible_later(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    assert run(engine, dd.enqueue_conversation_posts, cid) == 1
    _second_guild(engine, world)
    try:
        assert run(engine, dd.claim_next_post) is None
        assert [(r.status, r.last_error) for r in _post_rows(engine, cid)] == [
            ("needs_review", "ambiguous_target")
        ]
    finally:
        _drop_second_guild(engine)


@pytest.mark.parametrize(
    ("breakage", "other_gets_dm"),
    [
        ("update public.profiles set discord_dm_notifications = false where id = :p", True),
        ("update public.discord_connections set revoked_at = now() where profile_id = :p", True),
        (
            "update public.invitations set status = 'revoked', revoked_at = now() "
            "where auth_user_id = :p",
            True,
        ),
        # 削除要求を出した参加者がいる会話は双方から見えなくなるので、相手にも送らない
        ("update public.profiles set deleted_at = now() where id = :p", False),
    ],
)
def test_dm_requires_setting_and_link_at_enqueue_and_claim(
    engine: Engine, world: tuple[Person, Person], breakage: str, other_gets_dm: bool
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    with engine.begin() as conn:
        conn.execute(text(breakage), {"p": a.profile})
        assert dd.enqueue_dm_notifications(conn, cid) == int(other_gets_dm)
        conn.rollback()
    run(engine, dd.enqueue_dm_notifications, cid)
    with engine.begin() as conn:
        conn.execute(text(breakage), {"p": a.profile})
    claimed = [run(engine, dd.claim_next_dm), run(engine, dd.claim_next_dm)]
    assert [j.discord_user_id for j in claimed if j] == ([b.discord_id] if other_gets_dm else [])
    assert [(r.discord_user_id, r.status, r.last_error) for r in _dm_rows(engine, cid)] == [
        (a.discord_id, "cancelled", "not_eligible"),
        (b.discord_id, "sending", None)
        if other_gets_dm
        else (b.discord_id, "cancelled", "not_eligible"),
    ]


def test_dm_follows_web_notification(engine: Engine, world: tuple[Person, Person]) -> None:
    # Web 通知を OFF にした人には通知行がないので DM も作らない（DM は通知に結び付く）
    a, b = world
    with engine.begin() as conn:
        conn.execute(
            text("update public.profiles set web_notifications = false where id = :p"),
            {"p": a.profile},
        )
    cid = _conversation(engine, a, b)
    assert run(engine, dd.enqueue_dm_notifications, cid) == 1


def test_relinked_user_does_not_receive_old_dm(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_dm_notifications, cid)
    with engine.begin() as conn:
        conn.execute(
            text("update public.discord_connections set revoked_at = now() where profile_id = :p"),
            {"p": a.profile},
        )
        conn.execute(
            text(
                "insert into public.discord_connections (profile_id, discord_user_id) "
                "values (:p, '100000000000000099')"
            ),
            {"p": a.profile},
        )
    claimed = [run(engine, dd.claim_next_dm), run(engine, dd.claim_next_dm)]
    assert [j.discord_user_id for j in claimed if j] == [b.discord_id]


# -- 取り出しと結果 ---------------------------------------------------------------------------


def test_claim_returns_allowed_lines_in_order_and_marks_sending(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    job = run(engine, dd.claim_next_post)
    assert job is not None and job.conversation_id == cid and job.topic == "旅の話"
    assert job.lines == (dd.Line("AI1", "こんにちは @everyone"), dd.Line("AI2", "どうも"))
    assert job.attempts == 1
    assert _post_rows(engine, cid)[0].status == "sending"
    # 送信中の行は二度取り出さない
    assert run(engine, dd.claim_next_post) is None


def test_concurrent_claims_never_take_the_same_row(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    with engine.connect() as first, engine.connect() as second:
        with first.begin():
            assert dd.claim_next_post(first) is not None
            with second.begin():
                # 1 本目がロックしている間、2 本目は待たずに次（なし）へ進む
                assert dd.claim_next_post(second) is None


def test_retry_backs_off_then_gives_up(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    for attempt in range(1, dd.MAX_ATTEMPTS + 1):
        with engine.begin() as conn:
            conn.execute(text("update public.discord_deliveries set next_attempt_at = now()"))
        job = run(engine, dd.claim_next_post)
        assert job is not None and job.attempts == attempt
        run(engine, dd.mark_post_failed, job.delivery_id, "retry", "http_503:0")
        row = _post_rows(engine, cid)[0]
        if attempt < dd.MAX_ATTEMPTS:
            assert (row.status, row.last_error, row.later) == ("pending", "http_503:0", True)
    row = _post_rows(engine, cid)[0]
    assert (row.status, row.last_error) == ("cancelled", "gave_up:http_503:0")


def test_unknown_outcome_is_never_resent(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_conversation_posts, cid)
    job = run(engine, dd.claim_next_post)
    assert job is not None
    run(engine, dd.record_thread, job.delivery_id, "600000000000000001")
    run(engine, dd.mark_post_failed, job.delivery_id, "needs_review", "partial:TimeoutError")
    row = _post_rows(engine, cid)[0]
    assert (row.status, row.discord_thread_id) == ("needs_review", "600000000000000001")
    assert run(engine, dd.claim_next_post) is None
    # 結果の記録は sending の行にだけ効く（要確認を送信済みに書き換えない）
    run(engine, dd.mark_post_sent, job.delivery_id, "600000000000000001", "500000000000000001")
    assert _post_rows(engine, cid)[0].status == "needs_review"


def test_stale_sending_goes_to_review(engine: Engine, world: tuple[Person, Person]) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_after_completion, cid)
    assert run(engine, dd.claim_next_post) is not None
    assert run(engine, dd.claim_next_dm) is not None
    assert run(engine, dd.recover_stale_sending) == 0  # まだ新しい
    with engine.begin() as conn:
        for table in ("discord_deliveries", "discord_dm_deliveries"):
            conn.execute(text(f"alter table public.{table} disable trigger {table}_set_updated_at"))
            conn.execute(
                text(
                    f"update public.{table} set updated_at = now() - interval '11 minutes' "
                    "where status = 'sending'"
                )  # fmt: skip
            )
            conn.execute(text(f"alter table public.{table} enable trigger {table}_set_updated_at"))
    assert run(engine, dd.recover_stale_sending) == 2
    assert _post_rows(engine, cid)[0].status == "needs_review"


# -- 設定・ブロックによる取り消し -----------------------------------------------------------


def test_publish_off_block_and_dm_off_cancel_pending(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    c1, c2 = _conversation(engine, a, b), _conversation(engine, a, b)
    for cid in (c1, c2):
        run(engine, dd.enqueue_after_completion, cid)

    run(
        engine,
        settings.update_settings,
        a.profile,
        a.agent,
        {"discord_publish": False},
        max_daily_budget_usd=Decimal("1.00"),
        consent_version="v1",
    )
    assert {r.status for cid in (c1, c2) for r in _post_rows(engine, cid)} == {"cancelled"}

    with engine.begin() as conn:
        conn.execute(text("update public.discord_deliveries set status = 'pending'"))
    run(engine, settings.block_agent, b.profile, b.agent, a.agent)
    assert {r.status for cid in (c1, c2) for r in _post_rows(engine, cid)} == {"cancelled"}

    run(engine, settings.update_preferences, a.profile, {"discord_dm_notifications": False})
    dms = {(r.discord_user_id, r.status) for cid in (c1, c2) for r in _dm_rows(engine, cid)}
    assert dms == {(a.discord_id, "cancelled"), (b.discord_id, "pending")}


# -- Bot の送信ループ（偽の Discord） -------------------------------------------------------

THREAD = "600000000000000002"


class _Channel:
    class guild:  # noqa: N801
        me = object()

    async def create_thread(self, **_: Any) -> Any:
        # テキストチャンネルの目印。書き込みは REST 経由なので呼ばれない
        raise AssertionError("writes must go through the no-retry REST client")

    def permissions_for(self, _: object) -> Any:
        import discord

        return discord.Permissions(
            view_channel=True,
            send_messages=True,
            create_public_threads=True,
            send_messages_in_threads=True,
        )


class _Rest:
    def __init__(self) -> None:
        self.threads: list[str] = []
        self.messages: dict[str, list[str]] = {}
        self.dm_channels: dict[str, str] = {}
        self.count = 0

    async def create_thread(self, channel_id: str, name: str, archive_minutes: int) -> str:
        self.threads.append(channel_id)
        return THREAD

    async def send_message(self, channel_id: str, content: str) -> str:
        self.count += 1
        self.messages.setdefault(channel_id, []).append(content)
        return str(500000000000000000 + self.count)

    async def open_dm(self, user_id: str) -> str:
        return self.dm_channels.setdefault(user_id, f"70000000000000000{len(self.dm_channels)}")


class _Bot:
    web_url = WEB

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.channel = _Channel()
        self.rest = _Rest()

    async def in_transaction(self, work: Callable[[Connection], T]) -> T:
        def go() -> T:
            with self.engine.begin() as conn:
                return work(conn)

        return await asyncio.to_thread(go)

    def get_channel(self, _: int) -> Any:
        return self.channel

    async def fetch_channel(self, _: int) -> Any:
        raise AssertionError


def test_bot_loop_posts_thread_and_dms_end_to_end(
    engine: Engine, world: tuple[Person, Person]
) -> None:
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_after_completion, cid)
    bot = _Bot(engine)
    assert asyncio.run(delivery.process_once(bot)) == 3
    assert asyncio.run(delivery.process_once(bot)) == 0

    assert bot.rest.threads == [CHANNEL]
    sent = bot.rest.messages[THREAD]
    assert "AI が生成" in sent[0]
    assert sent[1] == "🤖 AI1（AI）\nこんにちは \\@everyone"
    assert sent[2] == "🤖 AI2（AI）\nどうも"
    assert "非表示の発言" not in "".join(sent)
    assert sent[-1].endswith(f"/conversations/{cid}")
    row = _post_rows(engine, cid)[0]
    assert (row.status, row.discord_thread_id, row.discord_message_id) == (
        "sent",
        THREAD,
        "500000000000000001",
    )
    for p in (a, b):
        channel = bot.rest.dm_channels[p.discord_id]
        assert bot.rest.messages[channel] == [f"新しい交流があります: {WEB}/conversations/{cid}"]
    assert {r.status for r in _dm_rows(engine, cid)} == {"sent"}


# -- 公開の撤回と送信の競合（#32） ----------------------------------------------------------
# 取り出し（Bot）と撤回（Web・Discord の操作）が同時に起きても、行き詰まらず（デッドロックや
# 長い待ちにならず）、次のどちらかに必ず決まる（docs/11「投稿と同期」）。
# - 撤回が先: その行は取り出されず cancelled になる
# - 取り出しが先: sending になった行は送信を続け、撤回は pending の行だけを取り消す


def _publish_off(conn: Connection, a: Person, b: Person) -> None:
    settings.update_settings(
        conn,
        a.profile,
        a.agent,
        {"discord_publish": False},
        max_daily_budget_usd=Decimal("1.00"),
        consent_version="v1",
    )


def _block(conn: Connection, a: Person, b: Person) -> None:
    settings.block_agent(conn, b.profile, b.agent, a.agent)


def _unlink(conn: Connection, a: Person, b: Person) -> None:
    from alter_domain import discord_link

    discord_link.disconnect(conn, a.profile)


def _dm_off(conn: Connection, a: Person, b: Person) -> None:
    settings.update_preferences(conn, a.profile, {"discord_dm_notifications": False})


WITHDRAWALS: dict[str, tuple[str, Callable[[Connection, Person, Person], None]]] = {
    "post-publish-off": ("post", _publish_off),
    "post-block": ("post", _block),
    "post-unlink": ("post", _unlink),
    "dm-off": ("dm", _dm_off),
    "dm-unlink": ("dm", _unlink),
}


@pytest.fixture
def withdrawal_audits(engine: Engine, world: tuple[Person, Person]) -> Iterator[None]:
    """連携解除が残す監査を消す（運用のテストが Discord の監査を数えるため）。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "delete from public.audit_events where action like 'discord.%' "
                "and actor_profile_id = any(:p)"
            ),
            {"p": [p.profile for p in world]},
        )


def _claim(conn: Connection, kind: str, a: Person) -> Any:
    if kind == "post":
        return dd.claim_next_post(conn)
    # 撤回するのは a の DM。b の DM は対象外なので、a の行が出るまで取り出す
    while (job := dd.claim_next_dm(conn)) is not None:
        if job.discord_user_id == a.discord_id:
            return job
    return None


def _status(engine: Engine, kind: str, cid: uuid.UUID, a: Person) -> str:
    if kind == "post":
        return str(_post_rows(engine, cid)[0].status)
    return next(str(r.status) for r in _dm_rows(engine, cid) if r.discord_user_id == a.discord_id)


@pytest.mark.parametrize("name", WITHDRAWALS)
def test_withdrawal_first_wins_over_claim(
    engine: Engine, world: tuple[Person, Person], withdrawal_audits: None, name: str
) -> None:
    kind, withdraw = WITHDRAWALS[name]
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_after_completion, cid)
    with engine.connect() as web, engine.connect() as bot:
        with web.begin():
            withdraw(web, a, b)
            with bot.begin():
                # 撤回の確定前でも、取り出しは待たずに（skip locked）その行を飛ばす
                bot.execute(text("set local lock_timeout = '2s'"))
                assert _claim(bot, kind, a) is None
    assert _status(engine, kind, cid, a) == "cancelled"
    with engine.begin() as conn:
        assert _claim(conn, kind, a) is None


@pytest.mark.parametrize("name", WITHDRAWALS)
def test_claim_first_keeps_sending_and_withdrawal_waits(
    engine: Engine, world: tuple[Person, Person], withdrawal_audits: None, name: str
) -> None:
    import threading

    kind, withdraw = WITHDRAWALS[name]
    a, b = world
    cid = _conversation(engine, a, b)
    run(engine, dd.enqueue_after_completion, cid)
    errors: list[BaseException] = []
    finished = threading.Event()

    def web_side() -> None:
        try:
            with engine.begin() as conn:
                conn.execute(text("set local lock_timeout = '10s'"))
                withdraw(conn, a, b)
        except BaseException as exc:  # noqa: BLE001 - 主スレッドで判定する
            errors.append(exc)
        finally:
            finished.set()

    with engine.connect() as bot:
        with bot.begin():
            job = _claim(bot, kind, a)
            assert job is not None
            worker = threading.Thread(target=web_side)
            worker.start()
            # 撤回は取り出し中の行のロックを待つ（先に進んで行を取り消さない）
            assert not finished.wait(0.5)
        worker.join(15)
    assert not worker.is_alive() and errors == []
    # 先に取り出された行は送信を続け、結果を記録できる
    assert _status(engine, kind, cid, a) == "sending"
    if kind == "post":
        run(engine, dd.mark_post_sent, job.delivery_id, "600000000000000009", "700000000000000009")
        assert _status(engine, kind, cid, a) == "sent"
    else:
        run(engine, dd.mark_dm_sent, job.delivery_id, "700000000000000009")
        assert _status(engine, kind, cid, a) == "sent"
    # 撤回のあとは、残りを取り出さない
    with engine.begin() as conn:
        assert _claim(conn, kind, a) is None
