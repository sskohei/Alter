-- Issue #6: プロフィール・AI人格・設定の基礎DDLとRLS
-- 正本: docs/05-database-design.md
--
-- 方針
-- * ID は UUID (gen_random_uuid)、時刻は TIMESTAMPTZ (UTC)。
-- * すべての FK に索引を付ける（UNIQUE / PK の先頭列で兼ねる場合を含む）。
-- * 業務テーブルは RLS を有効にし、ポリシーは 0 件 (default deny)。
--   anon / authenticated からは直接 SELECT/INSERT/UPDATE/DELETE できない。
--   データ操作は FastAPI（管理接続 + 所有権 SQL 条件）だけが行う。
-- * 承認済み人格は不変。既存の非公開欄の鍵ローテーションだけを例外とする。

-- ---------------------------------------------------------------------------
-- 共通: updated_at を自動更新するトリガー関数
-- ---------------------------------------------------------------------------
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles: 利用者（id は auth.users.id と同値）
-- ---------------------------------------------------------------------------
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name varchar(80) not null,
  web_notifications boolean not null default true,
  discord_dm_notifications boolean not null default false,
  onboarded_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint profiles_display_name_not_blank check (btrim(display_name) <> '')
);

create index profiles_deleted_at_idx on public.profiles (deleted_at);

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- agents: AI 本体（MVP は 1 人 1 体）
-- ---------------------------------------------------------------------------
create table public.agents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles (id) on delete cascade,
  name varchar(80) not null,
  icon_path text,
  status varchar(16) not null default 'draft',
  approved_profile_version integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- owner_id の FK 索引を兼ねる
  constraint agents_owner_id_key unique (owner_id),
  constraint agents_name_not_blank check (btrim(name) <> ''),
  constraint agents_status_check check (status in ('draft', 'active', 'paused', 'deleted')),
  -- 未承認の AI は交流可能 (active) にできない
  constraint agents_active_requires_approval check (
    status <> 'active' or approved_profile_version is not null
  )
);

create trigger agents_set_updated_at
before update on public.agents
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- agent_profiles: 人格版（承認済み版は不変）
-- ---------------------------------------------------------------------------
create table public.agent_profiles (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents (id) on delete cascade,
  version integer not null,
  personality text not null,
  values_text text not null,
  interests text[] not null default '{}',
  hobbies text[] not null default '{}',
  strengths text[] not null default '{}',
  weaknesses text[] not null default '{}',
  speaking_style text not null,
  interaction_policy text not null,
  shareable_info text not null,
  private_info_ciphertext bytea,
  private_info_key_version smallint,
  restrictions text not null,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  -- agent_id の FK 索引を兼ねる
  constraint agent_profiles_agent_id_version_key unique (agent_id, version),
  constraint agent_profiles_version_positive check (version > 0),
  -- 暗号文と鍵版は両方 NULL か両方非 NULL
  constraint agent_profiles_private_info_pair check (
    (private_info_ciphertext is null) = (private_info_key_version is null)
  ),
  -- 長さ・件数の上限（API 契約: テキスト各 2000 字、配列各 20 項目）
  constraint agent_profiles_text_length check (
    char_length(personality) <= 2000
    and char_length(values_text) <= 2000
    and char_length(speaking_style) <= 2000
    and char_length(interaction_policy) <= 2000
    and char_length(shareable_info) <= 2000
    and char_length(restrictions) <= 2000
  ),
  constraint agent_profiles_array_length check (
    cardinality(interests) <= 20
    and cardinality(hobbies) <= 20
    and cardinality(strengths) <= 20
    and cardinality(weaknesses) <= 20
  )
);

-- 承認済み版は不変。既存暗号文を別の鍵版へ移す更新だけを許可する。
-- 平文が同じことはDBでは検証できないため、管理APIの再暗号化処理が保証する。
create function public.prevent_approved_profile_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.approved_at is not null and new is distinct from old and (
    (to_jsonb(new) - 'private_info_ciphertext' - 'private_info_key_version')
      is distinct from
    (to_jsonb(old) - 'private_info_ciphertext' - 'private_info_key_version')
    or old.private_info_ciphertext is null
    or new.private_info_ciphertext is null
    or new.private_info_key_version is not distinct from old.private_info_key_version
  ) then
    raise exception 'approved agent profile % (agent %, version %) is immutable',
      old.id, old.agent_id, old.version
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger agent_profiles_prevent_approved_update
before update on public.agent_profiles
for each row execute function public.prevent_approved_profile_update();

-- agents.approved_profile_version は自分の人格版を指す（循環参照のため後付け）
alter table public.agents
  add constraint agents_approved_profile_fk
  foreign key (id, approved_profile_version)
  references public.agent_profiles (agent_id, version);

-- 指す人格版は承認済みでなければならない
create function public.ensure_approved_profile_version()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.approved_profile_version is not null and not exists (
    select 1
    from public.agent_profiles p
    where p.agent_id = new.id
      and p.version = new.approved_profile_version
      and p.approved_at is not null
  ) then
    raise exception 'agent % must reference an approved profile version (got %)',
      new.id, new.approved_profile_version
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger agents_ensure_approved_profile_version
before insert or update of approved_profile_version on public.agents
for each row execute function public.ensure_approved_profile_version();

-- ---------------------------------------------------------------------------
-- agent_settings: 交流設定
-- ---------------------------------------------------------------------------
create table public.agent_settings (
  agent_id uuid primary key references public.agents (id) on delete cascade,
  autonomy_enabled boolean not null default false,
  frequency_per_day smallint not null default 1,
  daily_budget_usd numeric(12, 6) not null default 0.20,
  max_session_usd numeric(12, 6) not null default 0.05,
  web_visibility varchar(16) not null default 'participants',
  discord_publish boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_settings_frequency_range check (frequency_per_day between 0 and 2),
  constraint agent_settings_daily_budget_nonnegative check (daily_budget_usd >= 0),
  constraint agent_settings_max_session_nonnegative check (max_session_usd >= 0),
  -- MVP は参加者のみ公開
  constraint agent_settings_web_visibility_check check (web_visibility = 'participants')
);

create trigger agent_settings_set_updated_at
before update on public.agent_settings
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- audit_events: 監査（本文・秘密・トークンは記録しない。180 日保持）
-- 削除済み利用者の記録も残すため actor_profile_id に FK は張らない。
-- ---------------------------------------------------------------------------
create table public.audit_events (
  id uuid primary key default gen_random_uuid(),
  actor_profile_id uuid,
  action varchar(64) not null,
  resource_type text not null,
  resource_id text not null,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now(),
  constraint audit_events_metadata_object check (jsonb_typeof(metadata) = 'object')
);

create index audit_events_actor_created_at_idx
  on public.audit_events (actor_profile_id, created_at desc);

-- ---------------------------------------------------------------------------
-- RLS: default deny
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.agents enable row level security;
alter table public.agent_profiles enable row level security;
alter table public.agent_settings enable row level security;
alter table public.audit_events enable row level security;

-- Supabase は public の新規テーブルに anon/authenticated への権限を既定付与するため明示的に外す。
-- (RLS ポリシー 0 件との二重の防御)
revoke all on table
  public.profiles,
  public.agents,
  public.agent_profiles,
  public.agent_settings,
  public.audit_events
from anon, authenticated;

revoke all on function
  public.set_updated_at(),
  public.prevent_approved_profile_update(),
  public.ensure_approved_profile_version()
from public, anon, authenticated;

-- 後続 migration で作るテーブル・関数にも同じ原則を既定で適用する
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on functions from public, anon, authenticated;

-- PUBLIC EXECUTE はグローバル既定値。スキーマ単位の REVOKE では取り消せない。
alter default privileges for role postgres
  revoke execute on functions from public;
