"""Discord への会話投稿と DM 通知の outbox（#28、docs/11「投稿と同期」）。

Discord には触れず DB だけを扱う。送信は Bot（``alter_discord_bot.delivery``）が行う。

流れ
1. 会話の完了トランザクションで ``enqueue_after_completion`` を呼び、配信行（pending）を作る
2. Bot が ``claim_next_post`` / ``claim_next_dm`` で 1 件ずつ取り出す。取り出す直前に条件を
   確認し直し、満たさなければ ``cancelled``。満たせば ``sending`` にしてコミットしてから送る
3. 送信結果を ``mark_*`` で記録する。成功が不明なものは自動で再送せず ``needs_review``

排他制御
- 取り出しは ``for update skip locked`` で配信行だけをロックする。確認し直しの読み取りでは
  同意・連携・設定の行をロックしない（連携解除や削除要求は「連携→同意→配信」の順でロック
  するので、配信行のあとに同意をロックすると交差してデッドロックする）
- 連携解除などと同時に起きた場合、先に ``sending`` になった行は送信を続ける。解除側は
  ``pending`` だけを取り消す（#26 の ``cancel_pending_deliveries`` と同じ扱い）
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Connection, text

from alter_domain.activity import _VISIBLE_CONVERSATION, NOTIFICATION_CONVERSATION_COMPLETED

DISCORD_MESSAGE_LIMIT = 2000
THREAD_NAME_LIMIT = 100
MAX_ATTEMPTS = 5
# 送信中のまま止まった行（Bot の異常終了など）を needs_review に回すまでの時間
STALE_SENDING_MINUTES = 10

# 投稿してよい会話か（会話 c・投稿先 ch・ギルド g を参照する）
# - 会話は完了・Web で見える範囲（90 日・削除要求なし・削除済み AI なし）
# - 投稿先とギルドが有効
# - 参加者 2 人とも: 会話開始時の Discord 公開の同意、AI の全体設定 ON、稼働中の AI、
#   削除要求なし、有効な招待、有効な Discord 連携、そのギルドへの有効な同意
# - どちらかがもう一方をブロックしていない
# 投稿先は 1 会話につき 1 つ。候補が複数あるときは送らず保留する（docs/09・#28）
_POSTABLE = (
    "c.status = 'completed' and " + _VISIBLE_CONVERSATION + " "
    "and ch.enabled and g.enabled "
    "and (select count(*) from public.conversation_participants p "
    "  join public.agents a on a.id = p.agent_id and a.status = 'active' "
    "  join public.agent_settings s on s.agent_id = a.id and s.discord_publish "
    "  join public.profiles u on u.id = a.owner_id and u.deleted_at is null "
    "  join public.invitations i on i.auth_user_id = u.id and i.status = 'active' "
    "  join public.agent_discord_consents k on k.agent_id = a.id and k.guild_id = g.id "
    "    and k.revoked_at is null "
    "  where p.conversation_id = c.id and p.discord_consent "
    "    and exists (select 1 from public.discord_connections dc "
    "      where dc.profile_id = u.id and dc.revoked_at is null)) = 2 "
    "and not exists (select 1 from public.conversation_participants p1 "
    "  join public.conversation_participants p2 on p2.conversation_id = p1.conversation_id "
    "    and p2.agent_id <> p1.agent_id "
    "  join public.agent_blocks b on b.blocker_agent_id = p1.agent_id "
    "    and b.blocked_agent_id = p2.agent_id "
    "  where p1.conversation_id = c.id)"
)

# DM を送ってよい通知か（通知 n・利用者 u・連携 dc を参照する）
_DM_SENDABLE = (
    "n.kind = :kind and n.created_at > now() - interval '90 days' "
    "and u.deleted_at is null and u.discord_dm_notifications "
    "and dc.revoked_at is null "
    "and exists (select 1 from public.invitations i "
    "  where i.auth_user_id = u.id and i.status = 'active') "
    "and (n.conversation_id is null or exists (select 1 from public.conversations c "
    "  where c.id = n.conversation_id and " + _VISIBLE_CONVERSATION + "))"
)


@dataclass(frozen=True)
class Line:
    agent_name: str
    content: str


@dataclass(frozen=True)
class PostJob:
    delivery_id: uuid.UUID
    channel_id: str
    conversation_id: uuid.UUID
    topic: str | None
    lines: tuple[Line, ...]
    attempts: int


@dataclass(frozen=True)
class DmJob:
    delivery_id: uuid.UUID
    discord_user_id: str
    conversation_id: uuid.UUID | None
    attempts: int


# -- 配信行の作成 ---------------------------------------------------------------------------


def _postable_channels(conn: Connection, conversation_id: uuid.UUID) -> list[str]:
    return list(
        conn.execute(
            text(
                "select ch.id from public.conversations c "
                "cross join public.discord_channels ch "
                "join public.discord_guilds g on g.id = ch.guild_id "
                "where c.id = :id and " + _POSTABLE + " order by ch.id"
            ),
            {"id": conversation_id},
        ).scalars()
    )


def enqueue_conversation_posts(conn: Connection, conversation_id: uuid.UUID) -> int:
    """完了した会話の投稿行を作る（再実行しても増えない）。作った行の件数を返す。

    - 条件を満たす投稿先が 1 つなら ``pending`` で 1 件
    - 2 つ以上（両者が複数のギルドに同意している）なら、どれにも送らず候補を
      ``needs_review``（``ambiguous_target``）で残し、運営・管理者が選ぶ（docs/09）
    取り消し済み（cancelled）の行は ``on conflict do nothing`` のため復活させない。
    """
    channels = _postable_channels(conn, conversation_id)
    if not channels:
        return 0
    held = len(channels) > 1
    created = 0
    for channel in channels:
        created += int(
            conn.execute(
                text(
                    "insert into public.discord_deliveries "
                    "(conversation_id, channel_id, status, last_error) "
                    "values (:c, :ch, :status, :error) "
                    "on conflict (conversation_id, channel_id) do nothing"
                ),
                {
                    "c": conversation_id,
                    "ch": channel,
                    "status": "needs_review" if held else "pending",
                    "error": "ambiguous_target" if held else None,
                },
            ).rowcount
        )
    return created


def enqueue_dm_notifications(conn: Connection, conversation_id: uuid.UUID) -> int:
    """会話完了の Web 通知のうち、DM 通知を ON にした連携済み本人の分の DM を 1 件ずつ作る。

    DM は Web 通知 1 件に結び付く（``discord_dm_deliveries.notification_id``）。Web 通知を
    OFF にしている人は通知行がないため、DM も作らない（docs/11 に記載）。
    """
    return int(
        conn.execute(
            text(
                "insert into public.discord_dm_deliveries (notification_id, discord_user_id) "
                "select n.id, dc.discord_user_id from public.notifications n "
                "join public.profiles u on u.id = n.profile_id "
                "join public.discord_connections dc on dc.profile_id = u.id "
                "where n.conversation_id = :id and " + _DM_SENDABLE + " "
                "on conflict (notification_id) do nothing"
            ),
            {"id": conversation_id, "kind": NOTIFICATION_CONVERSATION_COMPLETED},
        ).rowcount
    )


def enqueue_after_completion(conn: Connection, conversation_id: uuid.UUID) -> tuple[int, int]:
    """会話完了トランザクションから呼ぶ入口（Worker #19/#61）。

    ``activity.notify_conversation_completed`` のあとに、同じトランザクションで呼ぶ。
    戻り値は（作った投稿の件数、作った DM の件数）。
    """
    return (
        enqueue_conversation_posts(conn, conversation_id),
        enqueue_dm_notifications(conn, conversation_id),
    )


# -- 未配信の取り消し（DM 通知の OFF） -----------------------------------------------------------
# 公開設定・自律交流の OFF とブロックでの未投稿の取り消しは
# interaction_settings.cancel_pending_posts（#24）が担う


def cancel_pending_dms_for_profile(conn: Connection, profile_id: uuid.UUID) -> int:
    """DM 通知を OFF にしたとき、本人あての未送信 DM を取り消す。"""
    return int(
        conn.execute(
            text(
                "update public.discord_dm_deliveries d set status = 'cancelled', "
                "last_error = 'dm_off' "
                "from public.notifications n "
                "where n.id = d.notification_id and n.profile_id = :p and d.status = 'pending'"
            ),
            {"p": profile_id},
        ).rowcount
    )


# -- 取り出し ---------------------------------------------------------------------------------


def claim_next_post(conn: Connection) -> PostJob | None:
    """送信する投稿を 1 件取り出して ``sending`` にする。なければ None。

    条件を満たさなくなった行は ``cancelled`` にして次を見る。呼び出し側はこの関数の
    トランザクションをコミットしてから Discord へ送る。
    """
    while True:
        row = conn.execute(
            text(
                "select id, conversation_id, channel_id, attempts "
                "from public.discord_deliveries "
                "where status = 'pending' and next_attempt_at <= now() "
                "order by next_attempt_at, id for update skip locked limit 1"
            )
        ).first()
        if row is None:
            return None
        ok = conn.execute(
            text(
                "select c.topic from public.conversations c "
                "join public.discord_channels ch on ch.id = :ch "
                "join public.discord_guilds g on g.id = ch.guild_id "
                "where c.id = :c and " + _POSTABLE
            ),
            {"c": row.conversation_id, "ch": row.channel_id},
        ).first()
        if ok is None:
            _set_post(conn, row.id, "cancelled", "not_eligible")
            continue
        if len(_postable_channels(conn, row.conversation_id)) > 1:
            # 作成後に別のギルドでも条件がそろった。どこへ送るか決まらないので保留する
            _set_post(conn, row.id, "needs_review", "ambiguous_target")
            continue
        lines = tuple(
            Line(m.name, m.content)
            for m in conn.execute(
                text(
                    "select a.name, m.content from public.messages m "
                    "join public.agents a on a.id = m.agent_id "
                    "where m.conversation_id = :c and m.moderation_status = 'allowed' "
                    "order by m.turn_no"
                ),
                {"c": row.conversation_id},
            )
        )
        if not lines:
            _set_post(conn, row.id, "cancelled", "no_messages")
            continue
        conn.execute(
            text(
                "update public.discord_deliveries set status = 'sending', "
                "attempts = attempts + 1, last_error = null where id = :id"
            ),
            {"id": row.id},
        )
        return PostJob(
            delivery_id=row.id,
            channel_id=row.channel_id,
            conversation_id=row.conversation_id,
            topic=ok.topic,
            lines=lines,
            attempts=row.attempts + 1,
        )


def claim_next_dm(conn: Connection) -> DmJob | None:
    """送信する DM を 1 件取り出して ``sending`` にする。なければ None。"""
    while True:
        row = conn.execute(
            text(
                "select id, notification_id, discord_user_id, attempts "
                "from public.discord_dm_deliveries "
                "where status = 'pending' and next_attempt_at <= now() "
                "order by next_attempt_at, id for update skip locked limit 1"
            )
        ).first()
        if row is None:
            return None
        ok = conn.execute(
            text(
                "select n.conversation_id from public.notifications n "
                "join public.profiles u on u.id = n.profile_id "
                "join public.discord_connections dc on dc.profile_id = u.id "
                "  and dc.discord_user_id = :d "
                "where n.id = :n and " + _DM_SENDABLE
            ),
            {
                "n": row.notification_id,
                "d": row.discord_user_id,
                "kind": NOTIFICATION_CONVERSATION_COMPLETED,
            },
        ).first()
        if ok is None:
            _set_dm(conn, row.id, "cancelled", "not_eligible")
            continue
        conn.execute(
            text(
                "update public.discord_dm_deliveries set status = 'sending', "
                "attempts = attempts + 1, last_error = null where id = :id"
            ),
            {"id": row.id},
        )
        return DmJob(
            delivery_id=row.id,
            discord_user_id=row.discord_user_id,
            conversation_id=ok.conversation_id,
            attempts=row.attempts + 1,
        )


# -- 結果の記録 -------------------------------------------------------------------------------

Outcome = Literal["retry", "cancelled", "needs_review"]


def record_thread(conn: Connection, delivery_id: uuid.UUID, thread_id: str) -> None:
    """スレッドを作った直後に ID を残す（途中で失敗したとき、管理者が照合できるように）。"""
    conn.execute(
        text(
            "update public.discord_deliveries set discord_thread_id = :t "
            "where id = :id and status = 'sending'"
        ),
        {"id": delivery_id, "t": thread_id},
    )


def mark_post_sent(
    conn: Connection, delivery_id: uuid.UUID, thread_id: str, message_id: str
) -> None:
    conn.execute(
        text(
            "update public.discord_deliveries set status = 'sent', discord_thread_id = :t, "
            "discord_message_id = :m, last_error = null where id = :id and status = 'sending'"
        ),
        {"id": delivery_id, "t": thread_id, "m": message_id},
    )


def mark_post_failed(conn: Connection, delivery_id: uuid.UUID, outcome: Outcome, code: str) -> None:
    _fail(conn, "discord_deliveries", delivery_id, outcome, code)


def mark_dm_sent(conn: Connection, delivery_id: uuid.UUID, message_id: str) -> None:
    conn.execute(
        text(
            "update public.discord_dm_deliveries set status = 'sent', discord_message_id = :m, "
            "last_error = null where id = :id and status = 'sending'"
        ),
        {"id": delivery_id, "m": message_id},
    )


def mark_dm_failed(conn: Connection, delivery_id: uuid.UUID, outcome: Outcome, code: str) -> None:
    _fail(conn, "discord_dm_deliveries", delivery_id, outcome, code)


def recover_stale_sending(conn: Connection, *, minutes: int = STALE_SENDING_MINUTES) -> int:
    """送信中のまま止まった行を ``needs_review`` にする（自動では再送しない）。

    スレッド削除（``discord_cleanup_tasks``）は何度試しても重複しないので ``pending`` に戻す。
    """
    total = int(
        conn.execute(
            text(
                "update public.discord_cleanup_tasks set status = 'pending' "
                "where status = 'sending' and updated_at < now() - make_interval(mins => :m)"
            ),
            {"m": minutes},
        ).rowcount
    )
    for table in ("discord_deliveries", "discord_dm_deliveries"):
        total += int(
            conn.execute(
                text(
                    f"update public.{table} set status = 'needs_review', "  # noqa: S608
                    "last_error = 'stale_sending' where status = 'sending' "
                    "and updated_at < now() - make_interval(mins => :m)"
                ),
                {"m": minutes},
            ).rowcount
        )
    return total


def _fail(
    conn: Connection, table: str, delivery_id: uuid.UUID, outcome: Outcome, code: str
) -> None:
    code = code[:100]
    if outcome == "retry":
        # 送られていないことが確かな失敗だけ。間隔は 2^回数 分（最大 60 分）、5 回で打ち切る
        conn.execute(
            text(
                f"update public.{table} set "  # noqa: S608
                "status = case when attempts >= :max then 'cancelled' else 'pending' end, "
                "last_error = case when attempts >= :max "
                "  then 'gave_up:' || :code else :code end, "
                "next_attempt_at = now() "
                "  + make_interval(mins => least(power(2, attempts)::int, 60)) "
                "where id = :id and status = 'sending'"
            ),
            {"id": delivery_id, "code": code, "max": MAX_ATTEMPTS},
        )
        return
    conn.execute(
        text(
            f"update public.{table} set status = :s, last_error = :code "  # noqa: S608
            "where id = :id and status = 'sending'"
        ),
        {"id": delivery_id, "s": outcome, "code": code},
    )


def _set_post(conn: Connection, delivery_id: uuid.UUID, status: str, code: str) -> None:
    conn.execute(
        text("update public.discord_deliveries set status = :s, last_error = :e where id = :id"),
        {"id": delivery_id, "s": status, "e": code},
    )


def _set_dm(conn: Connection, delivery_id: uuid.UUID, status: str, code: str) -> None:
    conn.execute(
        text("update public.discord_dm_deliveries set status = :s, last_error = :e where id = :id"),
        {"id": delivery_id, "s": status, "e": code},
    )


# -- 本文の組み立て（Discord に送る文字列） -----------------------------------------------------

# Discord の Markdown・メンション・時刻表記・リンクとして解釈される文字
_ESCAPE = set("\\*_~`|>#[]()<-:@")


def escape_markdown(value: str) -> str:
    return "".join("\\" + ch if ch in _ESCAPE else ch for ch in value)


def _split(value: str, limit: int) -> list[str]:
    """``limit`` 文字以内に分ける。エスケープの ``\\`` と次の文字は分けない。改行を優先。"""
    parts: list[str] = []
    while len(value) > limit:
        cut = value.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
            # 末尾の連続した \ が奇数個なら、エスケープの途中なので 1 文字戻す
            trailing = len(value[:cut]) - len(value[:cut].rstrip("\\"))
            if trailing % 2 == 1:
                cut -= 1
        parts.append(value[:cut])
        value = value[cut:].lstrip("\n")
    if value:
        parts.append(value)
    return parts


def thread_name(job: PostJob) -> str:
    base = (job.topic or "").strip() or "AI 同士の交流"
    name = f"Alter: {base}"
    return name[:THREAD_NAME_LIMIT]


def render_post(job: PostJob, web_url: str) -> list[str]:
    """スレッドに順に送るメッセージ（どれも 2000 文字以内）。

    - 冒頭で AI が生成した会話であることを示す
    - 各発言は「🤖 AI名（AI）」のラベル付き。AI 名と本文はエスケープする
    - 最後に Web の会話詳細のリンク（参加者本人だけが開ける）
    """
    messages = ["Alter の AI 分身どうしの交流です（発言はすべて AI が生成したものです）。"]
    for line in job.lines:
        label = f"🤖 {escape_markdown(line.agent_name)}（AI）\n"
        for i, part in enumerate(
            _split(escape_markdown(line.content), DISCORD_MESSAGE_LIMIT - len(label))
        ):
            messages.append((label if i == 0 else "") + part)
    messages.append(
        f"参加者は Web で詳細を確認できます: {web_url}/conversations/{job.conversation_id}"
    )
    return messages


def render_dm(job: DmJob, web_url: str) -> str:
    """DM は固定の文言とリンクだけ（会話の本文・相手の情報は含めない）。"""
    if job.conversation_id is None:
        return f"Alter に新しいお知らせがあります: {web_url}/dashboard"
    return f"新しい交流があります: {web_url}/conversations/{job.conversation_id}"


# -- アカウント削除に伴うスレッド削除（#30） -------------------------------------------------

CleanupResult = Literal["deleted", "forbidden", "not_found", "retry"]


@dataclass(frozen=True)
class CleanupJob:
    task_id: uuid.UUID
    thread_id: str
    attempts: int


def claim_next_cleanup(conn: Connection) -> CleanupJob | None:
    """削除を試すスレッドを 1 件取り出して ``sending`` にする。なければ None。"""
    row = conn.execute(
        text(
            "select id, thread_id, attempts from public.discord_cleanup_tasks "
            "where status = 'pending' and next_attempt_at <= now() "
            "order by next_attempt_at, id for update skip locked limit 1"
        )
    ).first()
    if row is None:
        return None
    conn.execute(
        text(
            "update public.discord_cleanup_tasks set status = 'sending', "
            "attempts = attempts + 1, last_error = null where id = :id"
        ),
        {"id": row.id},
    )
    return CleanupJob(row.id, row.thread_id, row.attempts + 1)


def mark_cleanup(conn: Connection, task_id: uuid.UUID, result: CleanupResult, code: str) -> None:
    """結果を記録する。削除は何度試しても重複しないので、分からない失敗は再試行する。

    再試行は 2^回数 分（最大 60 分）、``MAX_ATTEMPTS`` 回で ``failed``。
    """
    code = code[:100]
    if result == "retry":
        conn.execute(
            text(
                "update public.discord_cleanup_tasks set "
                "status = case when attempts >= :max then 'failed' else 'pending' end, "
                "completed_at = case when attempts >= :max then now() end, "
                "last_error = :code, next_attempt_at = now() "
                "  + make_interval(mins => least(power(2, attempts)::int, 60)) "
                "where id = :id and status = 'sending'"
            ),
            {"id": task_id, "code": code, "max": MAX_ATTEMPTS},
        )
        return
    conn.execute(
        text(
            "update public.discord_cleanup_tasks set status = :s, completed_at = now(), "
            "last_error = :code where id = :id and status = 'sending'"
        ),
        {"id": task_id, "s": result, "code": code or None},
    )
