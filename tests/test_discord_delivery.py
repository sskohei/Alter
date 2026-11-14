"""#28: 投稿・DM の本文の組み立てと、送信結果の分類（Discord にも DB にも接続しない）。"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import discord
import pytest
from alter_discord_bot import delivery
from alter_domain import discord_delivery as dd

WEB = "https://alter.example"
CONV = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def _job(*lines: tuple[str, str], topic: str | None = "本の話") -> dd.PostJob:
    return dd.PostJob(
        delivery_id=uuid.uuid4(),
        channel_id="300000000000000001",
        conversation_id=CONV,
        topic=topic,
        lines=tuple(dd.Line(n, c) for n, c in lines),
        attempts=1,
    )


# -- 本文 -----------------------------------------------------------------------------------


def test_escape_neutralizes_markdown_mentions_and_links() -> None:
    raw = "@everyone <@123> **太字** `code` ```block``` [x](https://e.x) # 見出し > 引用 -# 小"
    escaped = dd.escape_markdown(raw)
    # 記号の前には必ず \ が付く（Markdown・メンション・リンクとして解釈されない）
    for i, ch in enumerate(escaped):
        if ch in "@<>*`[]()#-:":
            assert escaped[i - 1] == "\\", (i, escaped)
    # 表示上は元の文字が残る（バックスラッシュを外すと元に戻る）
    assert escaped.replace("\\", "") == raw.replace("\\", "")


def test_render_post_labels_ai_and_keeps_every_message_within_limit() -> None:
    long = "あ" * 3990 + "**"
    job = _job(("ミカ*", "こんにちは @here"), ("ソラ", long))
    messages = dd.render_post(job, WEB)
    assert all(len(m) <= dd.DISCORD_MESSAGE_LIMIT for m in messages)
    assert "AI が生成" in messages[0]
    assert messages[1].startswith("🤖 ミカ\\*（AI）\n")
    assert messages[1].endswith("こんにちは \\@here")
    assert messages[2].startswith("🤖 ソラ（AI）\n")
    # 長い発言は分割され、エスケープ（\*）の途中では切れない
    body = "".join(m.removeprefix("🤖 ソラ（AI）\n") for m in messages[2:-1])
    assert body == dd.escape_markdown(long)
    assert messages[-1].endswith(f"{WEB}/conversations/{CONV}")


def test_split_never_breaks_an_escape_sequence() -> None:
    value = dd.escape_markdown("a" + "*" * 50)
    for limit in range(2, 20):
        parts = dd._split(value, limit)
        assert "".join(parts) == value
        assert all(len(p) <= limit for p in parts)
        assert all(not p.startswith("*") for p in parts)


def test_thread_name_is_short_and_has_fallback() -> None:
    assert dd.thread_name(_job(("a", "b"), topic=None)) == "Alter: AI 同士の交流"
    assert len(dd.thread_name(_job(("a", "b"), topic="長" * 300))) == dd.THREAD_NAME_LIMIT


def test_dm_is_link_only() -> None:
    job = dd.DmJob(uuid.uuid4(), "100000000000000001", CONV, 1)
    assert dd.render_dm(job, WEB) == f"新しい交流があります: {WEB}/conversations/{CONV}"


# -- 失敗の分類 -------------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "x"


def _http(cls: type[discord.HTTPException], status: int) -> discord.HTTPException:
    return cls(_Resp(status), {"code": 50001, "message": "m"})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("exc", "phase", "outcome"),
    [
        (_http(discord.Forbidden, 403), "write", "cancelled"),
        (_http(discord.NotFound, 404), "read", "cancelled"),
        (_http(discord.HTTPException, 400), "write", "cancelled"),
        (_http(discord.DiscordServerError, 503), "read", "retry"),
        (_http(discord.DiscordServerError, 503), "write", "needs_review"),
        (_http(discord.HTTPException, 429), "write", "needs_review"),
        (TimeoutError(), "read", "retry"),
        (TimeoutError(), "write", "needs_review"),
        (_http(discord.Forbidden, 403), "partial", "needs_review"),
        (OSError(), "partial", "needs_review"),
    ],
)
def test_classify(exc: BaseException, phase: delivery.Phase, outcome: str) -> None:
    assert delivery.classify(exc, phase)[0] == outcome


# -- 送信（Discord を模した偽物で確認する） ---------------------------------------------------


class _Msg:
    def __init__(self, n: int) -> None:
        self.id = 500000000000000000 + n


class _Thread:
    id = 600000000000000001

    def __init__(self, fail_at: int | None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.fail_at = fail_at

    async def send(self, content: str, **kwargs: Any) -> _Msg:
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise TimeoutError()
        self.sent.append((content, kwargs))
        return _Msg(len(self.sent))


class _Guild:
    me = object()


class _Channel:
    guild = _Guild()

    def __init__(self, can_post: bool = True, create_error: BaseException | None = None,
                 fail_at: int | None = None) -> None:  # fmt: skip
        self.can_post = can_post
        self.create_error = create_error
        self.thread = _Thread(fail_at)
        self.created: list[dict[str, Any]] = []

    def permissions_for(self, _: object) -> discord.Permissions:
        return discord.Permissions(
            view_channel=True,
            send_messages=True,
            create_public_threads=self.can_post,
            send_messages_in_threads=True,
        )

    async def create_thread(self, **kwargs: Any) -> _Thread:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(kwargs)
        return self.thread


class _User:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, content: str, **kwargs: Any) -> _Msg:
        if self.error is not None:
            raise self.error
        self.sent.append((content, kwargs))
        return _Msg(1)


class _Bot:
    web_url = WEB

    def __init__(self, channel: Any = None, user: Any = None) -> None:
        self.channel = channel
        self.user = user

    async def in_transaction(self, work: Callable[[Any], Any]) -> Any:
        return work(None)

    def get_channel(self, _: int) -> Any:
        return self.channel

    async def fetch_channel(self, _: int) -> Any:
        raise _http(discord.NotFound, 404)

    def get_user(self, _: int) -> Any:
        return self.user

    async def fetch_user(self, _: int) -> Any:
        raise _http(discord.NotFound, 404)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    recorded: list[tuple[Any, ...]] = []
    for name in ("record_thread", "mark_post_sent", "mark_post_failed", "mark_dm_sent",
                 "mark_dm_failed"):  # fmt: skip
        monkeypatch.setattr(dd, name, lambda _c, *a, _n=name: recorded.append((_n, *a)))
    return recorded


def test_post_creates_thread_and_sends_without_mentions(calls: list[tuple[Any, ...]]) -> None:
    channel = _Channel()
    job = _job(("ミカ", "やあ"), ("ソラ", "どうも"))
    asyncio.run(delivery.send_post(_Bot(channel), job))
    assert channel.created[0]["type"] == discord.ChannelType.public_thread
    assert len(channel.thread.sent) == 4
    for _, kwargs in channel.thread.sent:
        mentions = kwargs["allowed_mentions"]
        assert mentions.everyone is False and mentions.users is False and mentions.roles is False
        assert kwargs["suppress_embeds"] is True
    assert calls == [
        ("record_thread", job.delivery_id, str(_Thread.id)),
        ("mark_post_sent", job.delivery_id, str(_Thread.id), str(_Msg(1).id)),
    ]


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (_Channel(can_post=False), ("cancelled", "missing_permissions")),
        (None, ("cancelled", "http_404:50001")),
        (_Channel(create_error=_http(discord.Forbidden, 403)), ("cancelled", "http_403:50001")),
        (_Channel(create_error=TimeoutError()), ("needs_review", "TimeoutError")),
        (_Channel(fail_at=1), ("needs_review", "partial:TimeoutError")),
    ],
)
def test_post_failures(
    calls: list[tuple[Any, ...]], channel: Any, expected: tuple[str, str]
) -> None:
    job = _job(("ミカ", "やあ"))
    asyncio.run(delivery.send_post(_Bot(channel), job))
    assert calls[-1] == ("mark_post_failed", job.delivery_id, *expected)
    assert not any(c[0] == "mark_post_sent" for c in calls)


def test_dm_sends_link_only_and_forbidden_is_not_retried(calls: list[tuple[Any, ...]]) -> None:
    job = dd.DmJob(uuid.uuid4(), "100000000000000001", CONV, 1)
    user = _User()
    asyncio.run(delivery.send_dm(_Bot(user=user), job))
    assert user.sent[0][0] == dd.render_dm(job, WEB)
    assert calls == [("mark_dm_sent", job.delivery_id, str(_Msg(1).id))]

    calls.clear()
    asyncio.run(delivery.send_dm(_Bot(user=_User(_http(discord.Forbidden, 403))), job))
    assert calls == [("mark_dm_failed", job.delivery_id, "cancelled", "dm_forbidden")]
