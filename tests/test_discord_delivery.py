"""#28: 投稿・DM の本文の組み立てと、送信結果の分類（Discord にも DB にも接続しない）。"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import discord
import pytest
from alter_discord_bot import delivery
from alter_discord_bot.rest import RestError
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
        (RestError(403, 50013), "write", "cancelled"),
        (RestError(500, 0), "write", "needs_review"),
        (RestError(502, 0), "read", "retry"),
        (RestError(429, 0), "write", "needs_review"),
    ],
)
def test_classify(exc: BaseException, phase: delivery.Phase, outcome: str) -> None:
    assert delivery.classify(exc, phase)[0] == outcome


# -- 送信（Discord を模した偽物で確認する） ---------------------------------------------------


class _Guild:
    me = object()


class _Channel:
    guild = _Guild()

    def __init__(self, can_post: bool = True) -> None:
        self.can_post = can_post

    async def create_thread(self, **_: Any) -> Any:
        # テキストチャンネルの目印。書き込みは REST 経由なので呼ばれない
        raise AssertionError("writes must go through the no-retry REST client")

    def permissions_for(self, _: object) -> discord.Permissions:
        return discord.Permissions(
            view_channel=True,
            send_messages=True,
            create_public_threads=self.can_post,
            send_messages_in_threads=True,
        )


class _Rest:
    """書き込みの偽物。``fail_at`` 回目の送信（スレッド作成を含めて数える）で失敗する。"""

    def __init__(self, fail_at: int | None = None, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_at = fail_at
        self.error = error or TimeoutError()

    def _maybe_fail(self) -> None:
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise self.error

    async def create_thread(self, channel_id: str, name: str, archive_minutes: int) -> str:
        self._maybe_fail()
        self.calls.append(("thread", channel_id, name))
        return "600000000000000001"

    async def send_message(self, channel_id: str, content: str) -> str:
        self._maybe_fail()
        self.calls.append(("message", channel_id, content))
        return str(500000000000000000 + len(self.calls))

    async def open_dm(self, user_id: str) -> str:
        self.calls.append(("dm", user_id))
        return "700000000000000001"


class _Bot:
    web_url = WEB

    def __init__(self, channel: Any = None, rest: _Rest | None = None) -> None:
        self.channel = channel
        self.rest = rest or _Rest()

    async def in_transaction(self, work: Callable[[Any], Any]) -> Any:
        return work(None)

    def get_channel(self, _: int) -> Any:
        return self.channel

    async def fetch_channel(self, _: int) -> Any:
        raise _http(discord.NotFound, 404)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    recorded: list[tuple[Any, ...]] = []
    for name in ("record_thread", "mark_post_sent", "mark_post_failed", "mark_dm_sent",
                 "mark_dm_failed"):  # fmt: skip
        monkeypatch.setattr(dd, name, lambda _c, *a, _n=name: recorded.append((_n, *a)))
    return recorded


def test_post_creates_one_thread_and_sends_in_order(calls: list[tuple[Any, ...]]) -> None:
    bot = _Bot(_Channel())
    job = _job(("ミカ", "やあ"), ("ソラ", "どうも"))
    asyncio.run(delivery.send_post(bot, job))
    kinds = [c[0] for c in bot.rest.calls]
    assert kinds == ["thread", "message", "message", "message", "message"]
    assert bot.rest.calls[0][1] == job.channel_id
    assert all(c[1] == "600000000000000001" for c in bot.rest.calls[1:])
    assert calls == [
        ("record_thread", job.delivery_id, "600000000000000001"),
        ("mark_post_sent", job.delivery_id, "600000000000000001", "500000000000000002"),
    ]


@pytest.mark.parametrize(
    ("bot", "expected"),
    [
        (_Bot(_Channel(can_post=False)), ("cancelled", "missing_permissions")),
        (_Bot(None), ("cancelled", "http_404:50001")),
        (_Bot(_Channel(), _Rest(0, RestError(403, 50013))), ("cancelled", "http_403:50013")),
        (_Bot(_Channel(), _Rest(0, RestError(500, 0))), ("needs_review", "http_500:0")),
        (_Bot(_Channel(), _Rest(0)), ("needs_review", "TimeoutError")),
        (_Bot(_Channel(), _Rest(2)), ("needs_review", "partial:TimeoutError")),
    ],
)
def test_post_failures(calls: list[tuple[Any, ...]], bot: _Bot, expected: tuple[str, str]) -> None:
    job = _job(("ミカ", "やあ"))
    asyncio.run(delivery.send_post(bot, job))
    assert calls[-1] == ("mark_post_failed", job.delivery_id, *expected)
    assert not any(c[0] == "mark_post_sent" for c in calls)
    # 失敗したあとに同じ書き込みを自分で送り直さない
    assert len([c for c in bot.rest.calls if c[0] == "thread"]) <= 1


def test_dm_sends_link_only_and_forbidden_is_not_retried(calls: list[tuple[Any, ...]]) -> None:
    job = dd.DmJob(uuid.uuid4(), "100000000000000001", CONV, 1)
    bot = _Bot()
    asyncio.run(delivery.send_dm(bot, job))
    assert bot.rest.calls == [
        ("dm", "100000000000000001"),
        ("message", "700000000000000001", dd.render_dm(job, WEB)),
    ]
    assert calls == [("mark_dm_sent", job.delivery_id, "500000000000000002")]

    calls.clear()
    blocked = _Bot(rest=_Rest(1, RestError(403, 50007)))
    asyncio.run(delivery.send_dm(blocked, job))
    assert calls == [("mark_dm_failed", job.delivery_id, "cancelled", "dm_forbidden")]
