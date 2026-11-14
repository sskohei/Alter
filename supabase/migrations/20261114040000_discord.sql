-- Issue #26: Discord 連携・ギルド・同意・配信 outbox の DDL
-- 正本: docs/05-database-design.md、docs/06-authentication-authorization.md、docs/11-discord-bot.md
--
-- 方針（#6・#13 と同じ）
-- * Discord の ID（snowflake）は TEXT。数字 15〜21 桁だけを受け付ける。
-- * RLS 有効・ポリシー 0 件（default deny）。anon / authenticated の直接アクセスなし。
-- * Discord のユーザーアクセストークンは保存しない（列を持たない）。identify 取得後に破棄する。
-- * OAuth state は平文を保存せず SHA-256 の 16 進ハッシュだけを持つ。10 分・使い捨て。
-- * 削除動作は docs/05「削除・保持」のとおり:
--   - 連携・state・同意は 利用者 / AI / ギルドの削除で CASCADE
--   - 投稿 outbox は会話削除で CASCADE。投稿先チャンネルは配信履歴がある間は消せない（RESTRICT）
--   - DM outbox は通知削除で CASCADE

-- snowflake の形式チェック（数字 15〜21 桁）
create function public.is_discord_snowflake(value text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select value ~ '^[0-9]{15,21}$'
$$;

-- ---------------------------------------------------------------------------
-- discord_connections: Web 本人と Discord ユーザーの紐付け
-- 有効な連携（revoked_at is null）は 利用者ごとに 1 件、Discord ユーザーごとに 1 件。
-- ---------------------------------------------------------------------------
create table public.discord_connections (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references public.profiles (id) on delete cascade,
  discord_user_id text not null,
  connected_at timestamptz not null default now(),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  constraint discord_connections_user_id_format check (
    public.is_discord_snowflake(discord_user_id)
  ),
  constraint discord_connections_time_order check (
    revoked_at is null or revoked_at >= connected_at
  )
);

create unique index discord_connections_active_profile_key
  on public.discord_connections (profile_id) where revoked_at is null;
create unique index discord_connections_active_user_key
  on public.discord_connections (discord_user_id) where revoked_at is null;
-- profile_id の FK 索引（失効済みの行も含む）
create index discord_connections_profile_id_idx on public.discord_connections (profile_id);
-- 失効後 30 日の非秘密メタデータの削除用
create index discord_connections_revoked_at_idx
  on public.discord_connections (revoked_at) where revoked_at is not null;

-- ---------------------------------------------------------------------------
-- discord_oauth_states: OAuth の CSRF 防止（本人 ID と結び付けた短命・使い捨て state）
-- ---------------------------------------------------------------------------
create table public.discord_oauth_states (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references public.profiles (id) on delete cascade,
  state_hash text not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now(),
  constraint discord_oauth_states_state_hash_key unique (state_hash),
  constraint discord_oauth_states_state_hash_format check (state_hash ~ '^[0-9a-f]{64}$'),
  -- 有効期間は最大 10 分
  constraint discord_oauth_states_ttl check (
    expires_at > created_at and expires_at <= created_at + interval '10 minutes'
  )
);

create index discord_oauth_states_profile_id_idx on public.discord_oauth_states (profile_id);
create index discord_oauth_states_expires_at_idx on public.discord_oauth_states (expires_at);

-- ---------------------------------------------------------------------------
-- discord_guilds: Bot を導入したギルド（Bot 離脱時は enabled=false）
-- ---------------------------------------------------------------------------
create table public.discord_guilds (
  id text primary key,
  name varchar(100),
  admin_discord_user_id text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint discord_guilds_id_format check (public.is_discord_snowflake(id)),
  constraint discord_guilds_admin_format check (
    public.is_discord_snowflake(admin_discord_user_id)
  ),
  constraint discord_guilds_name_check check (btrim(name) <> '')
);

create index discord_guilds_admin_idx on public.discord_guilds (admin_discord_user_id);

create trigger discord_guilds_set_updated_at
before update on public.discord_guilds
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- discord_channels: 管理者が /server setup で選んだ投稿先（MVP は 1 ギルド 1 投稿先）
-- 配信履歴がある間は削除せず enabled=false にする。
-- ---------------------------------------------------------------------------
create table public.discord_channels (
  id text primary key,
  guild_id text not null references public.discord_guilds (id) on delete cascade,
  name varchar(100),
  enabled boolean not null default true,
  configured_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint discord_channels_id_format check (public.is_discord_snowflake(id)),
  constraint discord_channels_configured_by_format check (
    public.is_discord_snowflake(configured_by)
  ),
  constraint discord_channels_name_check check (btrim(name) <> '')
);

create unique index discord_channels_active_guild_key
  on public.discord_channels (guild_id) where enabled;
create index discord_channels_guild_id_idx on public.discord_channels (guild_id);

create trigger discord_channels_set_updated_at
before update on public.discord_channels
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- agent_discord_consents: AI ごとのギルド別公開同意（/agent publish）
-- ---------------------------------------------------------------------------
create table public.agent_discord_consents (
  agent_id uuid not null references public.agents (id) on delete cascade,
  guild_id text not null references public.discord_guilds (id) on delete cascade,
  consented_at timestamptz not null default now(),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  primary key (agent_id, guild_id),
  constraint agent_discord_consents_time_order check (
    revoked_at is null or revoked_at >= consented_at
  )
);

-- 配信時に「このギルドで同意している AI」を引く。guild_id の FK 索引も兼ねる
create index agent_discord_consents_guild_idx
  on public.agent_discord_consents (guild_id, agent_id);

-- ---------------------------------------------------------------------------
-- discord_deliveries: 会話投稿の outbox（会話 × 投稿先で 1 件）
-- ---------------------------------------------------------------------------
create table public.discord_deliveries (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  channel_id text not null references public.discord_channels (id) on delete restrict,
  status varchar(16) not null default 'pending',
  discord_thread_id text,
  discord_message_id text,
  attempts smallint not null default 0,
  next_attempt_at timestamptz not null default now(),
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- conversation_id の FK 索引を兼ねる。同じ会話を同じ投稿先へ二重に投稿しない
  constraint discord_deliveries_conversation_channel_key unique (conversation_id, channel_id),
  constraint discord_deliveries_status_check check (
    status in ('pending', 'sending', 'sent', 'needs_review', 'cancelled')
  ),
  constraint discord_deliveries_attempts_nonnegative check (attempts >= 0),
  constraint discord_deliveries_ids_format check (
    (discord_thread_id is null or public.is_discord_snowflake(discord_thread_id))
    and (discord_message_id is null or public.is_discord_snowflake(discord_message_id))
  ),
  -- 例外の詳細や本文を残さない（短い固定コードを想定）
  constraint discord_deliveries_last_error_length check (char_length(last_error) <= 500)
);

create index discord_deliveries_status_next_attempt_idx
  on public.discord_deliveries (status, next_attempt_at);
create index discord_deliveries_channel_id_idx on public.discord_deliveries (channel_id);

create trigger discord_deliveries_set_updated_at
before update on public.discord_deliveries
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- discord_dm_deliveries: 個別通知（DM）の outbox（通知 1 件につき 1 件）
-- ---------------------------------------------------------------------------
create table public.discord_dm_deliveries (
  id uuid primary key default gen_random_uuid(),
  notification_id uuid not null references public.notifications (id) on delete cascade,
  discord_user_id text not null,
  status varchar(16) not null default 'pending',
  discord_message_id text,
  attempts smallint not null default 0,
  next_attempt_at timestamptz not null default now(),
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- notification_id の FK 索引を兼ねる
  constraint discord_dm_deliveries_notification_key unique (notification_id),
  constraint discord_dm_deliveries_status_check check (
    status in ('pending', 'sending', 'sent', 'needs_review', 'cancelled')
  ),
  constraint discord_dm_deliveries_attempts_nonnegative check (attempts >= 0),
  constraint discord_dm_deliveries_ids_format check (
    public.is_discord_snowflake(discord_user_id)
    and (discord_message_id is null or public.is_discord_snowflake(discord_message_id))
  ),
  constraint discord_dm_deliveries_last_error_length check (char_length(last_error) <= 500)
);

create index discord_dm_deliveries_status_next_attempt_idx
  on public.discord_dm_deliveries (status, next_attempt_at);
-- 連携解除時の未配信取消（Discord ユーザー単位）
create index discord_dm_deliveries_user_status_idx
  on public.discord_dm_deliveries (discord_user_id, status);

create trigger discord_dm_deliveries_set_updated_at
before update on public.discord_dm_deliveries
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS: default deny（#6 と同じ原則）
-- ---------------------------------------------------------------------------
alter table public.discord_connections enable row level security;
alter table public.discord_oauth_states enable row level security;
alter table public.discord_guilds enable row level security;
alter table public.discord_channels enable row level security;
alter table public.agent_discord_consents enable row level security;
alter table public.discord_deliveries enable row level security;
alter table public.discord_dm_deliveries enable row level security;

revoke all on table
  public.discord_connections,
  public.discord_oauth_states,
  public.discord_guilds,
  public.discord_channels,
  public.agent_discord_consents,
  public.discord_deliveries,
  public.discord_dm_deliveries
from anon, authenticated;

revoke all on function public.is_discord_snowflake(text)
from public, anon, authenticated;
