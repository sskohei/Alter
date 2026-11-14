"""Bot が投稿先チャンネルで必要な権限（docs/11）。管理者権限は求めない。"""

import discord

REQUIRED_CHANNEL_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "create_public_threads",
    "send_messages_in_threads",
)


def bot_can_post(permissions: discord.Permissions) -> bool:
    return all(getattr(permissions, name) for name in REQUIRED_CHANNEL_PERMISSIONS)
