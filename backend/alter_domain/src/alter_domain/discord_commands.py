"""Discord Bot のスラッシュコマンドの共通ドメイン処理（#27、docs/11）。

Bot（apps/discord_bot）は Discord から受け取った値と、Discord 側で確認した権限だけを渡す。
ここでは DB だけを使い、Discord の API には触れない（テストは Discord なしで行う）。

本人の解決:
- Discord のユーザー ID を ``discord_connections`` の有効な連携で利用者に結び付ける
  （表示名は認証の根拠にしない）
- 利用者が削除要求済み・招待が失効済みなら、未連携と同じく拒否する（停止中の人は操作できない）

応答はすべて ``Reply``。``ephemeral`` は原則 True（本人にだけ見える）。会話本文・非公開人格・
相手の情報は返さない。
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from alter_domain import interaction_settings as settings_domain

HISTORY_DEFAULT = 5
HISTORY_MAX = 10


@dataclass(frozen=True)
class Reply:
    text: str
    ephemeral: bool = True


@dataclass(frozen=True)
class Member:
    """コマンドを実行した本人（Discord 側の値）。"""

    discord_user_id: str


@dataclass(frozen=True)
class GuildContext:
    """コマンドが実行されたギルド（DM なら None）。"""

    guild_id: str
    guild_name: str | None


@dataclass(frozen=True)
class Linked:
    profile_id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str | None


def _not_linked(web_url: str) -> Reply:
    return Reply(
        "Alter と Discord が連携されていません。"
        f"Web の設定画面から連携してください: {web_url}/settings/discord"
    )


def resolve_member(conn: Connection, member: Member) -> Linked | None:
    """有効な連携・有効な招待・削除要求なしの本人だけを返す。"""
    row = conn.execute(
        text(
            "select c.profile_id, a.id as agent_id, a.name as agent_name "
            "from public.discord_connections c "
            "join public.profiles p on p.id = c.profile_id and p.deleted_at is null "
            "join public.invitations i on i.auth_user_id = c.profile_id and i.status = 'active' "
            "left join public.agents a on a.owner_id = c.profile_id and a.status <> 'deleted' "
            "where c.discord_user_id = :d and c.revoked_at is null"
        ),
        {"d": member.discord_user_id},
    ).first()
    if row is None:
        return None
    return Linked(row.profile_id, row.agent_id, row.agent_name)


def _audit(
    conn: Connection, actor: uuid.UUID | None, action: str, rid: str, meta: dict[str, Any]
) -> None:
    conn.execute(
        text(
            "insert into public.audit_events "
            "(actor_profile_id, action, resource_type, resource_id, metadata) "
            "values (:actor, :action, 'discord', :rid, cast(:meta as jsonb))"
        ),
        {"actor": actor, "action": action, "rid": rid, "meta": json.dumps(meta)},
    )


# -- /agent dashboard ---------------------------------------------------------------------


def dashboard(web_url: str) -> Reply:
    return Reply(f"Alter のダッシュボード: {web_url}/dashboard")


# -- /agent status ------------------------------------------------------------------------


def status(conn: Connection, member: Member, *, web_url: str) -> Reply:
    linked = resolve_member(conn, member)
    if linked is None:
        return _not_linked(web_url)
    if linked.agent_id is None:
        return Reply(f"まだ AI が作成されていません。Web で作成してください: {web_url}/agent/new")
    row = conn.execute(
        text(
            "select a.status, s.autonomy_enabled, s.frequency_per_day, s.daily_budget_usd, "
            "  s.discord_publish, "
            "  (select coalesce(sum(u.actual_usd), 0) from public.usage_records u "
            "   where u.agent_id = a.id and u.kind = 'charge' "
            "     and u.created_at >= date_trunc('day', now() at time zone 'utc') "
            "       at time zone 'utc') as spent "
            "from public.agents a "
            "left join public.agent_settings s on s.agent_id = a.id where a.id = :a"
        ),
        {"a": linked.agent_id},
    ).one()
    autonomy = bool(row.autonomy_enabled)
    budget = Decimal(row.daily_budget_usd or 0)
    remaining = max(budget - Decimal(row.spent), Decimal(0))
    state = {"draft": "下書き", "active": "稼働中", "paused": "停止中"}.get(row.status, row.status)
    lines = [
        f"AI: {linked.agent_name}（{state}）",
        f"自律交流: {'ON' if autonomy else 'OFF'}（1 日 {row.frequency_per_day or 0} 回まで）",
        f"今日の予算の残り: ${remaining:.2f} / ${budget:.2f}（UTC 基準・確定分のみ）",
        f"Discord 公開（全体設定）: {'ON' if row.discord_publish else 'OFF'}",
    ]
    return Reply("\n".join(lines))


# -- /agent profile -----------------------------------------------------------------------


def profile(conn: Connection, member: Member, agent_name: str | None, *, web_url: str) -> Reply:
    """承認済み人格の公開してよい欄だけを返す。MVP では本人の AI だけ（他人の AI は出さない）。"""
    linked = resolve_member(conn, member)
    if linked is None:
        return _not_linked(web_url)
    if linked.agent_id is None or (agent_name is not None and agent_name != linked.agent_name):
        return Reply("その AI は見つかりませんでした。")
    row = conn.execute(
        text(
            "select p.personality, p.interests, p.hobbies, p.speaking_style, p.shareable_info "
            "from public.agents a join public.agent_profiles p "
            "  on p.agent_id = a.id and p.version = a.approved_profile_version "
            "where a.id = :a and p.approved_at is not null"
        ),
        {"a": linked.agent_id},
    ).first()
    if row is None:
        return Reply("承認済みの人格がまだありません。Web で確認・承認してください。")
    lines = [
        f"AI: {linked.agent_name}",
        f"性格: {row.personality}",
        f"興味: {'、'.join(row.interests) or 'なし'}",
        f"趣味: {'、'.join(row.hobbies) or 'なし'}",
        f"話し方: {row.speaking_style}",
        f"共有してよい情報: {row.shareable_info or 'なし'}",
    ]
    return Reply("\n".join(lines))


# -- /agent history -----------------------------------------------------------------------


def history(conn: Connection, member: Member, limit: int | None, *, web_url: str) -> Reply:
    """本人の AI が参加した最近の会話を、相手の AI 名と Web のリンクだけで返す。

    会話本文は出さない（公開チャンネルに全文を出さない。応答も ephemeral）。
    """
    linked = resolve_member(conn, member)
    if linked is None:
        return _not_linked(web_url)
    if linked.agent_id is None:
        return Reply("まだ AI が作成されていません。")
    count = max(1, min(limit or HISTORY_DEFAULT, HISTORY_MAX))
    rows = conn.execute(
        text(
            "select c.id, c.status, c.created_at, other.name as partner "
            "from public.conversations c "
            "join public.conversation_participants me "
            "  on me.conversation_id = c.id and me.agent_id = :a "
            "join public.conversation_participants op "
            "  on op.conversation_id = c.id and op.agent_id <> :a "
            "join public.agents other on other.id = op.agent_id "
            "where c.created_at > now() - interval '90 days' "
            "and not exists (select 1 from public.conversation_participants hp "
            "  join public.agents ha on ha.id = hp.agent_id "
            "  join public.profiles hu on hu.id = ha.owner_id "
            "  where hp.conversation_id = c.id "
            "  and (hu.deleted_at is not null or ha.status = 'deleted')) "
            "order by c.created_at desc, c.id desc limit :n"
        ),
        {"a": linked.agent_id, "n": count},
    ).all()
    if not rows:
        return Reply("まだ交流の履歴はありません。")
    label = {
        "queued": "予定",
        "running": "進行中",
        "completed": "完了",
        "failed": "失敗",
        "cancelled": "取消",
    }
    lines = [
        f"{r.created_at.astimezone(UTC):%Y-%m-%d} {r.partner}（{label.get(r.status, r.status)}）"
        f" {web_url}/conversations/{r.id}"
        for r in rows
    ]
    return Reply("\n".join(lines))


# -- /agent pause・resume -----------------------------------------------------------------


def set_autonomy(
    conn: Connection,
    member: Member,
    enabled: bool,
    *,
    web_url: str,
    max_daily_budget_usd: Decimal,
    consent_version: str,
) -> Reply:
    """Web の交流設定と同じドメイン処理で ON/OFF する（同意・承認・予算の確認も同じ）。"""
    linked = resolve_member(conn, member)
    if linked is None:
        return _not_linked(web_url)
    if linked.agent_id is None:
        return Reply("まだ AI が作成されていません。")
    try:
        settings_domain.update_settings(
            conn,
            linked.profile_id,
            linked.agent_id,
            {"autonomy_enabled": enabled},
            max_daily_budget_usd=max_daily_budget_usd,
            consent_version=consent_version,
        )
    except settings_domain.SettingsConflict:
        return Reply(
            "今は自律交流を再開できません。AI の承認・予算・同意を Web で確認してください: "
            f"{web_url}/settings/interaction"
        )
    except settings_domain.SettingsInvalid:
        return Reply(f"予算の設定を確認してください: {web_url}/settings/interaction")
    return Reply("自律交流を再開しました。" if enabled else "自律交流を停止しました。")


# -- /agent publish -----------------------------------------------------------------------


def _publish_explanation(guild_name: str | None, channel_name: str | None) -> str:
    where = f"「{guild_name or 'このサーバー'}」の #{channel_name or '投稿先'}"
    return (
        f"このサーバーで公開すると、相手も同意した完了済みの交流が {where} に投稿されます。"
        "サーバーのメンバーが読めます。一度投稿されたものは、取り消しても完全には回収できません。"
        "Web の全体設定（Discord 公開）も ON にする必要があります。"
        "同意する場合は confirm を True にしてもう一度実行してください。"
    )


def publish(
    conn: Connection,
    member: Member,
    guild: GuildContext | None,
    on: bool,
    confirm: bool,
    *,
    web_url: str,
) -> Reply:
    """そのギルドだけの公開同意を記録・取消する。"""
    if guild is None:
        return Reply("このコマンドは公開したいサーバーの中で実行してください。")
    linked = resolve_member(conn, member)
    if linked is None:
        return _not_linked(web_url)
    if linked.agent_id is None:
        return Reply("まだ AI が作成されていません。")
    target = conn.execute(
        text(
            "select g.name as guild_name, ch.name as channel_name "
            "from public.discord_guilds g "
            "join public.discord_channels ch on ch.guild_id = g.id and ch.enabled "
            "where g.id = :g and g.enabled"
        ),
        {"g": guild.guild_id},
    ).first()
    if not on:
        revoked = conn.execute(
            text(
                "update public.agent_discord_consents set revoked_at = now() "
                "where agent_id = :a and guild_id = :g and revoked_at is null"
            ),
            {"a": linked.agent_id, "g": guild.guild_id},
        ).rowcount
        cancelled = conn.execute(
            text(
                "update public.discord_deliveries d set status = 'cancelled' "
                "from public.discord_channels ch "
                "where ch.id = d.channel_id and ch.guild_id = :g and d.status = 'pending' "
                "and exists (select 1 from public.conversation_participants p "
                "  where p.conversation_id = d.conversation_id and p.agent_id = :a)"
            ),
            {"a": linked.agent_id, "g": guild.guild_id},
        ).rowcount
        if revoked:
            _audit(
                conn,
                linked.profile_id,
                "discord.publish_off",
                guild.guild_id,
                {"posts_cancelled": cancelled},
            )
        return Reply("このサーバーでの公開をやめました。まだ投稿されていない交流も取り消しました。")
    if target is None:
        return Reply(
            "このサーバーでは投稿先がまだ設定されていません。"
            "サーバーの管理者に /server setup を実行してもらってください。"
        )
    if not confirm:
        return Reply(_publish_explanation(target.guild_name, target.channel_name))
    conn.execute(
        text(
            "insert into public.agent_discord_consents (agent_id, guild_id) values (:a, :g) "
            "on conflict (agent_id, guild_id) do update "
            "set consented_at = now(), revoked_at = null "
            "where public.agent_discord_consents.revoked_at is not null"
        ),
        {"a": linked.agent_id, "g": guild.guild_id},
    )
    _audit(conn, linked.profile_id, "discord.publish_on", guild.guild_id, {})
    publish_on = conn.execute(
        text("select discord_publish from public.agent_settings where agent_id = :a"),
        {"a": linked.agent_id},
    ).scalar_one_or_none()
    note = (
        ""
        if publish_on
        else f"\nWeb の全体設定（Discord 公開）が OFF のため、まだ投稿されません: "
        f"{web_url}/settings/interaction"
    )
    return Reply("このサーバーでの公開に同意しました。" + note)


# -- /server setup・Bot の離脱 ---------------------------------------------------------------


@dataclass(frozen=True)
class SetupTarget:
    channel_id: str
    channel_name: str | None
    #: 実行者がこのギルドの管理権限（Manage Server）を持つか（Discord 側で実行時に確認）
    invoker_can_manage: bool
    #: Bot がそのチャンネルで閲覧・投稿・公開スレッド作成・スレッド内投稿をできるか
    bot_can_post: bool


def server_setup(
    conn: Connection, member: Member, guild: GuildContext | None, target: SetupTarget
) -> Reply:
    """ギルドと投稿先を登録する。投稿先が変わったら、そのギルドの公開同意を取り消す（再同意が必要）。"""
    if guild is None:
        return Reply("このコマンドはサーバーの中で実行してください。")
    if not target.invoker_can_manage:
        return Reply("このコマンドはサーバーの管理権限を持つ人だけが実行できます。")
    if not target.bot_can_post:
        return Reply(
            "Bot がそのチャンネルに投稿できません。閲覧・メッセージ送信・公開スレッドの作成・"
            "スレッド内での送信を Bot に許可してください。"
        )
    conn.execute(
        text(
            "insert into public.discord_guilds (id, name, admin_discord_user_id) "
            "values (:g, :name, :admin) "
            "on conflict (id) do update set name = excluded.name, "
            "  admin_discord_user_id = excluded.admin_discord_user_id, enabled = true"
        ),
        {"g": guild.guild_id, "name": guild.guild_name, "admin": member.discord_user_id},
    )
    previous = conn.execute(
        text("select id from public.discord_channels where guild_id = :g and enabled for update"),
        {"g": guild.guild_id},
    ).scalar_one_or_none()
    changed = previous is not None and previous != target.channel_id
    revoked = 0
    if changed:
        conn.execute(
            text(
                "update public.discord_deliveries set status = 'cancelled' "
                "where channel_id = :old and status = 'pending'"
            ),
            {"old": previous},
        )
        conn.execute(
            text("update public.discord_channels set enabled = false where id = :old"),
            {"old": previous},
        )
        revoked = conn.execute(
            text(
                "update public.agent_discord_consents set revoked_at = now() "
                "where guild_id = :g and revoked_at is null"
            ),
            {"g": guild.guild_id},
        ).rowcount
    conn.execute(
        text(
            "insert into public.discord_channels (id, guild_id, name, configured_by) "
            "values (:c, :g, :name, :by) "
            "on conflict (id) do update set name = excluded.name, enabled = true, "
            "  configured_by = excluded.configured_by "
            "where public.discord_channels.guild_id = excluded.guild_id"
        ),
        {
            "c": target.channel_id,
            "g": guild.guild_id,
            "name": target.channel_name,
            "by": member.discord_user_id,
        },
    )
    _audit(
        conn,
        None,
        "discord.server_setup",
        guild.guild_id,
        {"channel_changed": changed, "consents_revoked": revoked},
    )
    if changed:
        return Reply(
            "投稿先を変更しました。これまでの公開同意は取り消したので、"
            "公開する人は /agent publish でもう一度同意してください。"
        )
    return Reply("投稿先を設定しました。公開する人は /agent publish で同意してください。")


def guild_removed(conn: Connection, guild_id: str) -> None:
    """Bot がギルドから外れたら無効化する（配信履歴の保持中は行を消さない）。"""
    conn.execute(
        text("update public.discord_guilds set enabled = false where id = :g"),
        {"g": guild_id},
    )
    conn.execute(
        text(
            "update public.discord_deliveries d set status = 'cancelled' "
            "from public.discord_channels ch "
            "where ch.id = d.channel_id and ch.guild_id = :g and d.status = 'pending'"
        ),
        {"g": guild_id},
    )
