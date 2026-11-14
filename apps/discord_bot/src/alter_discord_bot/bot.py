"""Discord Gateway に接続するスラッシュコマンド Bot（#27、docs/11）。

- Intents は ``guilds`` だけ（Message Content を要求しない。過去メッセージを読まない）
- コマンドの登録（同期）は ``--sync-commands`` を付けたデプロイ時だけ行う（起動のたびに同期しない）
- 応答は原則 ephemeral。DB 処理の前に defer して、Discord の 3 秒の応答期限を守る
- DB は同期の SQLAlchemy を ``asyncio.to_thread`` で呼ぶ（イベントループを止めない）
- 再接続は discord.py の自動再接続に任せる。SIGTERM で接続を閉じて終了する
- 権限（管理権限・Bot の投稿権限）は Discord 側の値を実行時に確認し、ドメイン処理へ渡す
"""

import asyncio
import logging
import signal
from collections.abc import Callable
from typing import TypeVar

import discord
from alter_domain import discord_commands as cmd
from alter_domain.db import create_db_engine
from alter_domain.settings import Settings
from discord import app_commands
from sqlalchemy import Connection, Engine

logger = logging.getLogger("alter_discord_bot")
T = TypeVar("T")

# Bot が投稿先チャンネルで必要な権限（docs/11）
REQUIRED_CHANNEL_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "create_public_threads",
    "send_messages_in_threads",
)


def intents() -> discord.Intents:
    value = discord.Intents.none()
    value.guilds = True
    return value


def bot_can_post(permissions: discord.Permissions) -> bool:
    return all(getattr(permissions, name) for name in REQUIRED_CHANNEL_PERMISSIONS)


def _member(interaction: discord.Interaction) -> cmd.Member:
    return cmd.Member(discord_user_id=str(interaction.user.id))


def _guild(interaction: discord.Interaction) -> cmd.GuildContext | None:
    if interaction.guild is None:
        return None
    return cmd.GuildContext(guild_id=str(interaction.guild.id), guild_name=interaction.guild.name)


class AlterBot(discord.Client):
    def __init__(self, settings: Settings, engine: Engine) -> None:
        super().__init__(intents=intents())
        self.settings = settings
        self.engine = engine
        self.web_url = settings.alter_web_url.rstrip("/")
        self.tree = app_commands.CommandTree(self)
        register_commands(self.tree, self)

    async def in_transaction(self, work: Callable[[Connection], T]) -> T:
        def run() -> T:
            with self.engine.begin() as conn:
                return work(conn)

        return await asyncio.to_thread(run)

    async def respond(
        self, interaction: discord.Interaction, work: Callable[[Connection], cmd.Reply]
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            reply = await self.in_transaction(work)
        except Exception:  # noqa: BLE001 - 本文や内部情報を利用者へ出さない
            logger.exception("command failed: %s", interaction.command and interaction.command.name)
            reply = cmd.Reply("処理できませんでした。時間をおいてもう一度お試しください。")
        await interaction.followup.send(reply.text, ephemeral=reply.ephemeral)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.in_transaction(lambda conn: cmd.guild_removed(conn, str(guild.id)))
        logger.info("left guild; disabled its delivery target")


def register_commands(tree: app_commands.CommandTree[AlterBot], bot: AlterBot) -> None:
    agent = app_commands.Group(name="agent", description="自分の AI 分身")
    server = app_commands.Group(
        name="server",
        description="サーバーの設定（管理者）",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @agent.command(name="status", description="自律交流の ON/OFF と今日の予算の残り")
    async def status(interaction: discord.Interaction) -> None:
        member = _member(interaction)
        await bot.respond(interaction, lambda c: cmd.status(c, member, web_url=bot.web_url))

    @agent.command(name="profile", description="自分の AI の公開してよい人格")
    @app_commands.describe(agent="AI の名前（省略すると自分の AI）")
    async def profile(interaction: discord.Interaction, agent: str | None = None) -> None:
        member = _member(interaction)
        await bot.respond(interaction, lambda c: cmd.profile(c, member, agent, web_url=bot.web_url))

    @agent.command(name="history", description="最近の交流（Web のリンクだけ）")
    @app_commands.describe(limit="表示する件数（1〜10）")
    async def history(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, cmd.HISTORY_MAX] | None = None,
    ) -> None:
        member = _member(interaction)
        await bot.respond(interaction, lambda c: cmd.history(c, member, limit, web_url=bot.web_url))

    @agent.command(name="pause", description="自律交流を止める")
    async def pause(interaction: discord.Interaction) -> None:
        member = _member(interaction)
        await bot.respond(interaction, lambda c: _set_autonomy(bot, c, member, False))

    @agent.command(name="resume", description="自律交流を再開する")
    async def resume(interaction: discord.Interaction) -> None:
        member = _member(interaction)
        await bot.respond(interaction, lambda c: _set_autonomy(bot, c, member, True))

    @agent.command(name="publish", description="このサーバーでの公開に同意する / やめる")
    @app_commands.describe(
        state="on で同意、off でやめる",
        confirm="説明を読んで同意する場合は True",
    )
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    @app_commands.guild_only()
    async def publish(
        interaction: discord.Interaction,
        state: app_commands.Choice[str],
        confirm: bool = False,
    ) -> None:
        member, guild = _member(interaction), _guild(interaction)
        on = state.value == "on"
        await bot.respond(
            interaction,
            lambda c: cmd.publish(c, member, guild, on, confirm, web_url=bot.web_url),
        )

    @agent.command(name="dashboard", description="Alter の Web ダッシュボード")
    async def dashboard(interaction: discord.Interaction) -> None:
        reply = cmd.dashboard(bot.web_url)
        await interaction.response.send_message(reply.text, ephemeral=reply.ephemeral)

    @server.command(name="setup", description="交流を投稿するチャンネルを設定する")
    @app_commands.describe(channel="投稿先のテキストチャンネル")
    async def setup(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        member, guild = _member(interaction), _guild(interaction)
        invoker = interaction.user
        can_manage = isinstance(invoker, discord.Member) and invoker.guild_permissions.manage_guild
        me = interaction.guild.me if interaction.guild is not None else None
        can_post = me is not None and bot_can_post(channel.permissions_for(me))
        target = cmd.SetupTarget(
            channel_id=str(channel.id),
            channel_name=channel.name,
            invoker_can_manage=can_manage,
            bot_can_post=can_post,
        )
        await bot.respond(interaction, lambda c: cmd.server_setup(c, member, guild, target))

    tree.add_command(agent)
    tree.add_command(server)


def _set_autonomy(bot: AlterBot, conn: Connection, member: cmd.Member, enabled: bool) -> cmd.Reply:
    return cmd.set_autonomy(
        conn,
        member,
        enabled,
        web_url=bot.web_url,
        max_daily_budget_usd=bot.settings.agent_daily_budget_max_usd,
        consent_version=bot.settings.alter_consent_version,
    )


async def run(settings: Settings, *, sync_commands: bool) -> None:
    token = settings.discord_bot_token.get_secret_value()
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is required in live mode")
    engine = create_db_engine(settings)
    bot = AlterBot(settings, engine)

    if sync_commands:
        # デプロイ時だけ: ログインしてコマンドを同期し、Gateway には接続せずに終了する
        async with bot:
            await bot.login(token)
            synced = await bot.tree.sync()
            logger.info("synced %d commands", len(synced))
        return

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, lambda: asyncio.ensure_future(bot.close()))
    async with bot:
        await bot.start(token, reconnect=True)
