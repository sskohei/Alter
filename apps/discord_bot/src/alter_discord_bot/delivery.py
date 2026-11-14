"""会話投稿と DM 通知の送信ループ（#28、docs/11「投稿と同期」）。

- DB の outbox（``alter_domain.discord_delivery``）から 1 件ずつ取り出して送る
- 取り出し（``sending`` にする）をコミットしてから Discord へ送る。送信中に DB を握らない
- 書き込み（スレッド作成・発言・DM）は内部で再送しない ``rest.DiscordRest`` で送る
  （discord.py は 5xx・接続断で書き込みも再送し、二重投稿になり得るため）
- 送ったか分からない失敗は自動で再送しない（``needs_review``）。送っていないことが確かな
  失敗だけ、間隔をあけて再試行する
- メンションは一切解決しない（``AllowedMentions.none()``）。URL の埋め込みも出さない
- レート制限（429）は Discord が処理していないので、``Retry-After`` に従って待って送り直す
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypeVar

import discord
from alter_domain import discord_delivery as dd
from sqlalchemy import Connection

from alter_discord_bot.permissions import bot_can_post
from alter_discord_bot.rest import RestError

logger = logging.getLogger("alter_discord_bot.delivery")
T = TypeVar("T")

POLL_SECONDS = 5.0
BATCH_PER_CYCLE = 5
THREAD_ARCHIVE_MINUTES = 1440

# read: まだ何も書き込んでいない / write: 作成・送信の要求を出した / partial: スレッドは作った
Phase = Literal["read", "write", "partial"]


class RestApi(Protocol):
    async def create_thread(self, channel_id: str, name: str, archive_minutes: int) -> str: ...

    async def send_message(self, channel_id: str, content: str) -> str: ...

    async def open_dm(self, user_id: str) -> str: ...


class DeliveryBot(Protocol):
    web_url: str

    @property
    def rest(self) -> RestApi: ...

    def in_transaction(self, work: Callable[[Connection], T]) -> Awaitable[T]: ...

    def get_channel(self, id: int, /) -> Any: ...

    def fetch_channel(self, channel_id: int, /) -> Awaitable[Any]: ...


def classify(exc: BaseException, phase: Phase) -> tuple[dd.Outcome, str]:
    """失敗を「再試行 / 取消 / 要確認」に分ける。"""
    if phase == "partial":
        # スレッドや一部の発言はもう公開されている。続きを自動で送ると重複する
        return "needs_review", f"partial:{_code(exc)}"
    status = _status(exc)
    if status is not None and 400 <= status < 500 and status != 429:
        # Discord が拒否した（作られていない）。権限・チャンネル・DM 拒否は待っても直らない
        return "cancelled", _code(exc)
    # 5xx・429・通信断・タイムアウト: 書き込みを出したあとなら成功したか分からない
    if phase == "write":
        return "needs_review", _code(exc)
    return "retry", _code(exc)


def _status(exc: BaseException) -> int | None:
    if isinstance(exc, RestError | discord.HTTPException):
        return int(exc.status)
    return None


def _code(exc: BaseException) -> str:
    if isinstance(exc, RestError | discord.HTTPException):
        return f"http_{exc.status}:{exc.code}"
    return type(exc).__name__


async def send_post(bot: DeliveryBot, job: dd.PostJob) -> None:
    phase: Phase = "read"
    try:
        channel = bot.get_channel(int(job.channel_id)) or await bot.fetch_channel(
            int(job.channel_id)
        )
        guild = getattr(channel, "guild", None)
        me = getattr(guild, "me", None)
        if not hasattr(channel, "create_thread") or me is None:
            await _post_failed(bot, job, "cancelled", "not_text_channel")
            return
        # 投稿直前にも Bot の権限を確かめる（管理者が権限を外したら送らない）
        if not bot_can_post(channel.permissions_for(me)):
            await _post_failed(bot, job, "cancelled", "missing_permissions")
            return
        phase = "write"
        thread_id = await bot.rest.create_thread(
            job.channel_id, dd.thread_name(job), THREAD_ARCHIVE_MINUTES
        )
        phase = "partial"
        await bot.in_transaction(lambda c: dd.record_thread(c, job.delivery_id, thread_id))
        first: str | None = None
        for content in dd.render_post(job, bot.web_url):
            sent = await bot.rest.send_message(thread_id, content)
            first = first or sent
        assert first is not None
        message_id = first
        await bot.in_transaction(
            lambda c: dd.mark_post_sent(c, job.delivery_id, thread_id, message_id)
        )
        logger.info("posted conversation delivery %s", job.delivery_id)
    except Exception as exc:  # noqa: BLE001 - 本文を含めず、分類したコードだけを残す
        outcome, code = classify(exc, phase)
        logger.warning("post delivery %s failed: %s (%s)", job.delivery_id, code, outcome)
        await _post_failed(bot, job, outcome, code)


async def send_dm(bot: DeliveryBot, job: dd.DmJob) -> None:
    phase: Phase = "read"
    try:
        # DM チャンネルを開くのは何度でも同じ結果（重複しない）なので read と同じ扱い
        channel_id = await bot.rest.open_dm(job.discord_user_id)
        phase = "write"
        message_id = await bot.rest.send_message(channel_id, dd.render_dm(job, bot.web_url))
        await bot.in_transaction(lambda c: dd.mark_dm_sent(c, job.delivery_id, message_id))
    except Exception as exc:  # noqa: BLE001
        outcome, code = classify(exc, phase)
        if _status(exc) == 403:
            # DM を受け付けない設定。Web 通知は残っているので、無限に再試行しない
            code = "dm_forbidden"
        logger.warning("dm delivery %s failed: %s (%s)", job.delivery_id, code, outcome)
        await _guard(
            bot.in_transaction(lambda c: dd.mark_dm_failed(c, job.delivery_id, outcome, code))
        )


async def delete_thread(bot: DeliveryBot, job: dd.CleanupJob) -> None:
    """アカウント削除で消えた会話のスレッドを削除する（#30）。

    スレッドの削除には「スレッドの管理」権限が要る。Bot に付いていなければ ``forbidden`` と
    記録して終える（運営が結果を確認し、必要なら管理者に手動削除を頼む）。
    """
    result: dd.CleanupResult
    code = ""
    try:
        thread = bot.get_channel(int(job.thread_id)) or await bot.fetch_channel(int(job.thread_id))
        await thread.delete()
        result = "deleted"
    except discord.Forbidden as exc:
        result, code = "forbidden", _code(exc)
    except discord.NotFound as exc:
        result, code = "not_found", _code(exc)
    except Exception as exc:  # noqa: BLE001 - 削除は重複しないので、分からない失敗は再試行
        result, code = "retry", _code(exc)
    logger.info("thread cleanup %s: %s", job.task_id, result)
    await _guard(bot.in_transaction(lambda c: dd.mark_cleanup(c, job.task_id, result, code)))


async def _post_failed(bot: DeliveryBot, job: dd.PostJob, outcome: dd.Outcome, code: str) -> None:
    await _guard(
        bot.in_transaction(lambda c: dd.mark_post_failed(c, job.delivery_id, outcome, code))
    )


async def _guard(work: Awaitable[None]) -> None:
    # 結果の記録に失敗しても、行は sending のまま残り、あとで needs_review になる
    try:
        await work
    except Exception:  # noqa: BLE001
        logger.exception("failed to record delivery result")


async def process_once(bot: DeliveryBot, *, batch: int = BATCH_PER_CYCLE) -> int:
    """止まった送信中を回収し、投稿・DM・スレッド削除をそれぞれ最大 ``batch`` 件処理する。"""
    await bot.in_transaction(dd.recover_stale_sending)
    handled = 0
    for _ in range(batch):
        post = await bot.in_transaction(dd.claim_next_post)
        if post is None:
            break
        await send_post(bot, post)
        handled += 1
    for _ in range(batch):
        dm = await bot.in_transaction(dd.claim_next_dm)
        if dm is None:
            break
        await send_dm(bot, dm)
        handled += 1
    for _ in range(batch):
        cleanup = await bot.in_transaction(dd.claim_next_cleanup)
        if cleanup is None:
            break
        await delete_thread(bot, cleanup)
        handled += 1
    return handled


async def run_loop(bot: DeliveryBot, *, interval: float = POLL_SECONDS) -> None:
    while True:
        try:
            handled = await process_once(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - DB 断などでもループは止めない
            logger.exception("delivery cycle failed")
            handled = 0
        if handled == 0:
            await asyncio.sleep(interval)
