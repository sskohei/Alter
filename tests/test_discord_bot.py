"""#27: Discord Bot の接続設定とコマンド定義（Discord に接続しない）。"""

import asyncio

import discord
import pytest
from alter_discord_bot.bot import AlterBot, bot_can_post, intents, run
from alter_domain import discord_commands as cmd
from alter_domain.settings import Settings
from sqlalchemy import create_engine


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_intents_are_minimal() -> None:
    value = intents()
    assert value.guilds is True
    # 本文・メンバー一覧・プレゼンスは要求しない（過去メッセージを集めない）
    assert value.message_content is False
    assert value.messages is False
    assert value.members is False
    assert value.presences is False


def test_commands_match_contract() -> None:
    async def build() -> AlterBot:
        return AlterBot(_settings(), create_engine("sqlite://"))

    bot = asyncio.run(build())
    groups = {c.name: c for c in bot.tree.get_commands()}
    assert set(groups) == {"agent", "server"}
    agent = groups["agent"]
    server = groups["server"]
    assert isinstance(agent, discord.app_commands.Group)
    assert isinstance(server, discord.app_commands.Group)
    assert {c.name for c in agent.commands} == {
        "status",
        "profile",
        "history",
        "pause",
        "resume",
        "publish",
        "dashboard",
    }
    assert {c.name for c in server.commands} == {"setup"}
    # /server は既定で管理権限のある人にだけ表示する（実行時にも確認する）
    assert server.default_permissions is not None
    assert server.default_permissions.manage_guild is True
    assert server.guild_only is True
    publish = next(c for c in agent.commands if c.name == "publish")
    assert publish.guild_only is True
    asyncio.run(bot.close())


def test_bot_can_post_requires_all_permissions() -> None:
    full = discord.Permissions(
        view_channel=True,
        send_messages=True,
        create_public_threads=True,
        send_messages_in_threads=True,
    )
    assert bot_can_post(full)
    for name in (
        "view_channel",
        "send_messages",
        "create_public_threads",
        "send_messages_in_threads",
    ):
        lacking = discord.Permissions(full.value)
        setattr(lacking, name, False)
        assert not bot_can_post(lacking), name
    # 管理者権限は求めない
    assert not full.administrator


def test_live_mode_requires_token() -> None:
    with pytest.raises(SystemExit):
        asyncio.run(run(_settings(alter_mode="live", discord_enabled=True), sync_commands=False))


def test_dashboard_reply_is_ephemeral_link() -> None:
    reply = cmd.dashboard("https://alter.example")
    assert reply.ephemeral and reply.text.endswith("https://alter.example/dashboard")
