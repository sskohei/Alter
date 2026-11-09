-- Issue #13: 会話・費用・ジョブ・記憶/関係性の DDL
-- 正本: docs/05-database-design.md、docs/09-autonomous-interaction.md、docs/10-memory-relationship.md
--
-- 方針（#6 と同じ）
-- * ID は UUID、時刻は TIMESTAMPTZ (UTC)、金額は NUMERIC(12,6) USD。すべての FK に索引。
-- * RLS 有効・ポリシー 0 件（default deny）。anon / authenticated の直接アクセスなし。
-- * 削除動作は docs/05「削除・保持」のとおり DDL で明示する。
--   - 会話を消すと 参加者・発言・通知・記憶 も消える（CASCADE）
--   - 会話に参加した AI は、会話が残っている間は消せない（RESTRICT）。
--     アカウント削除は「会話 → 記憶 → 通知 → AI」の順で消す（docs/05 トランザクション境界）
--   - ジョブ（30〜90 日）と費用台帳（12 か月）は会話（90 日）と保持期間が違うため SET NULL
-- * 重複防止の軸（docs/09「冪等性」）を UNIQUE で持つ:
--   background_jobs(kind,dedupe_key)、conversations.job_id、messages(conversation_id,turn_no)、
--   usage_records.idempotency_key、notifications(profile_id,dedupe_key)、memories(agent_id,dedupe_key)

-- ---------------------------------------------------------------------------
-- background_jobs: 永続ジョブ（Scheduler が予約、Worker が lease を取って実行）
-- ---------------------------------------------------------------------------
create table public.background_jobs (
  id uuid primary key default gen_random_uuid(),
  kind varchar(32) not null,
  dedupe_key text not null,
  payload jsonb not null default '{}',
  status varchar(16) not null default 'queued',
  run_at timestamptz not null default now(),
  lease_until timestamptz,
  attempts smallint not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint background_jobs_kind_dedupe_key_key unique (kind, dedupe_key),
  constraint background_jobs_kind_format check (kind ~ '^[a-z][a-z0-9_.]{0,31}$'),
  constraint background_jobs_dedupe_key_check check (
    btrim(dedupe_key) <> '' and char_length(dedupe_key) <= 200
  ),
  constraint background_jobs_status_check check (
    status in ('queued', 'running', 'retry_wait', 'completed', 'failed', 'cancelled')
  ),
  constraint background_jobs_payload_object check (jsonb_typeof(payload) = 'object'),
  constraint background_jobs_attempts_nonnegative check (attempts >= 0),
  -- running の間だけ lease を持つ
  constraint background_jobs_lease_only_when_running check (
    (status = 'running') = (lease_until is not null)
  ),
  -- 例外の詳細や本文を残さない（短い固定コードを想定）
  constraint background_jobs_last_error_length check (char_length(last_error) <= 500)
);

-- 待機中ジョブの取得（Worker: status in (queued, retry_wait) and run_at <= now() ... SKIP LOCKED）
create index background_jobs_waiting_idx
  on public.background_jobs (run_at)
  where status in ('queued', 'retry_wait');
-- lease 切れの再取得（status = running and lease_until < now()）
create index background_jobs_lease_idx
  on public.background_jobs (lease_until)
  where status = 'running';
-- docs/05 の (status, run_at)。保持期限のパージと監視指標（状態別件数・最古待機）用
create index background_jobs_status_run_at_idx on public.background_jobs (status, run_at);

create trigger background_jobs_set_updated_at
before update on public.background_jobs
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- conversations: 交流
-- ---------------------------------------------------------------------------
create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  status varchar(16) not null default 'queued',
  topic text,
  summary text,
  visibility varchar(16) not null default 'participants',
  started_at timestamptz,
  ended_at timestamptz,
  profile_versions jsonb not null default '{}',
  job_id uuid references public.background_jobs (id) on delete set null,
  cost_usd numeric(12, 6) not null default 0,
  created_at timestamptz not null default now(),
  -- job_id の FK 索引を兼ねる。1 ジョブ = 1 会話
  constraint conversations_job_id_key unique (job_id),
  constraint conversations_status_check check (
    status in ('queued', 'running', 'completed', 'failed', 'cancelled')
  ),
  -- MVP の Web 公開範囲は参加者本人だけ（agent_settings.web_visibility と同じ）
  constraint conversations_visibility_check check (visibility = 'participants'),
  constraint conversations_cost_nonnegative check (cost_usd >= 0),
  constraint conversations_profile_versions_object check (
    jsonb_typeof(profile_versions) = 'object'
  ),
  constraint conversations_time_order check (
    ended_at is null or started_at is null or ended_at >= started_at
  ),
  constraint conversations_text_length check (
    char_length(topic) <= 200 and char_length(summary) <= 4000
  )
);

create index conversations_started_at_idx on public.conversations (started_at desc);
-- 90 日保持のパージ用
create index conversations_created_at_idx on public.conversations (created_at);

-- ---------------------------------------------------------------------------
-- conversation_participants: 参加者（2 体）
-- ---------------------------------------------------------------------------
create table public.conversation_participants (
  conversation_id uuid not null references public.conversations (id) on delete cascade,
  agent_id uuid not null references public.agents (id) on delete restrict,
  ordinal smallint not null,
  discord_consent boolean not null,
  web_consent boolean not null,
  created_at timestamptz not null default now(),
  primary key (conversation_id, agent_id),
  constraint conversation_participants_ordinal_key unique (conversation_id, ordinal),
  constraint conversation_participants_ordinal_check check (ordinal in (1, 2))
);

-- agent_id の FK 索引。AI ごとの会話一覧・進行中セッション確認にも使う
create index conversation_participants_agent_id_idx
  on public.conversation_participants (agent_id, conversation_id);

-- ---------------------------------------------------------------------------
-- messages: 発言（1 会話最大 8 発言）
-- ---------------------------------------------------------------------------
create table public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null,
  agent_id uuid not null,
  turn_no smallint not null,
  content text not null,
  moderation_status varchar(16) not null,
  token_input integer not null default 0,
  token_output integer not null default 0,
  created_at timestamptz not null default now(),
  -- 発言者はその会話の参加者でなければならない。会話削除時は参加者経由で CASCADE
  constraint messages_participant_fk
    foreign key (conversation_id, agent_id)
    references public.conversation_participants (conversation_id, agent_id)
    on delete cascade,
  -- (conversation_id, turn_no) が発言保存の冪等キー。conversation_id の索引も兼ねる
  constraint messages_conversation_turn_key unique (conversation_id, turn_no),
  constraint messages_turn_no_range check (turn_no between 1 and 8),
  constraint messages_content_check check (
    btrim(content) <> '' and char_length(content) <= 4000
  ),
  constraint messages_moderation_status_check check (moderation_status in ('allowed', 'blocked')),
  constraint messages_tokens_nonnegative check (token_input >= 0 and token_output >= 0)
);

create index messages_participant_idx on public.messages (conversation_id, agent_id);

-- ---------------------------------------------------------------------------
-- usage_records: 費用台帳（reserve → charge → release）
-- ---------------------------------------------------------------------------
create table public.usage_records (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents (id) on delete cascade,
  conversation_id uuid references public.conversations (id) on delete set null,
  provider text not null,
  model text not null,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  estimated_usd numeric(12, 6) not null,
  actual_usd numeric(12, 6),
  kind varchar(16) not null,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  constraint usage_records_idempotency_key_key unique (idempotency_key),
  constraint usage_records_kind_check check (kind in ('reserve', 'charge', 'release')),
  constraint usage_records_provider_model_check check (
    btrim(provider) <> '' and char_length(provider) <= 64
    and btrim(model) <> '' and char_length(model) <= 128
  ),
  constraint usage_records_idempotency_key_check check (
    btrim(idempotency_key) <> '' and char_length(idempotency_key) <= 200
  ),
  constraint usage_records_tokens_nonnegative check (input_tokens >= 0 and output_tokens >= 0),
  constraint usage_records_amounts_nonnegative check (
    estimated_usd >= 0 and (actual_usd is null or actual_usd >= 0)
  )
);

-- 日次上限判定（AI ごとの当日の確定費用＋未解放予約）。agent_id の FK 索引を兼ねる
create index usage_records_agent_created_at_idx on public.usage_records (agent_id, created_at);
create index usage_records_conversation_id_idx on public.usage_records (conversation_id);

-- ---------------------------------------------------------------------------
-- notifications: Web 通知
-- ---------------------------------------------------------------------------
create table public.notifications (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references public.profiles (id) on delete cascade,
  kind varchar(32) not null,
  conversation_id uuid references public.conversations (id) on delete cascade,
  body jsonb not null default '{}',
  read_at timestamptz,
  dedupe_key text not null,
  created_at timestamptz not null default now(),
  -- profile_id の FK 索引を兼ねる
  constraint notifications_profile_dedupe_key_key unique (profile_id, dedupe_key),
  constraint notifications_kind_format check (kind ~ '^[a-z][a-z0-9_.]{0,31}$'),
  constraint notifications_body_object check (jsonb_typeof(body) = 'object'),
  constraint notifications_dedupe_key_check check (
    btrim(dedupe_key) <> '' and char_length(dedupe_key) <= 200
  )
);

create index notifications_profile_created_at_idx
  on public.notifications (profile_id, created_at desc);
create index notifications_conversation_id_idx on public.notifications (conversation_id);

-- ---------------------------------------------------------------------------
-- agent_blocks: ブロック（片側だけでもマッチング対象外）
-- ---------------------------------------------------------------------------
create table public.agent_blocks (
  blocker_agent_id uuid not null references public.agents (id) on delete cascade,
  blocked_agent_id uuid not null references public.agents (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (blocker_agent_id, blocked_agent_id),
  constraint agent_blocks_not_self check (blocker_agent_id <> blocked_agent_id)
);

-- 「誰かにブロックされているか」の確認と blocked_agent_id の FK 索引
create index agent_blocks_blocked_agent_id_idx on public.agent_blocks (blocked_agent_id);

-- ---------------------------------------------------------------------------
-- memories: 長期記憶（本人視点、出典つき）
-- ---------------------------------------------------------------------------
create table public.memories (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents (id) on delete cascade,
  source_conversation_id uuid references public.conversations (id) on delete cascade,
  text text not null,
  importance smallint not null,
  dedupe_key text not null,
  status varchar(16) not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- agent_id の FK 索引を兼ねる。MVP は同じ候補を棄却する
  constraint memories_agent_dedupe_key_key unique (agent_id, dedupe_key),
  constraint memories_importance_range check (importance between 1 and 5),
  constraint memories_status_check check (status in ('active', 'archived')),
  constraint memories_text_check check (btrim(text) <> '' and char_length(text) <= 1000),
  constraint memories_dedupe_key_check check (
    btrim(dedupe_key) <> '' and char_length(dedupe_key) <= 200
  )
);

create index memories_agent_status_importance_idx
  on public.memories (agent_id, status, importance);
create index memories_source_conversation_id_idx on public.memories (source_conversation_id);

create trigger memories_set_updated_at
before update on public.memories
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- agent_relationships: AI 関係（無向。ID を昇順に正規化）
-- ---------------------------------------------------------------------------
create table public.agent_relationships (
  agent_low_id uuid not null references public.agents (id) on delete cascade,
  agent_high_id uuid not null references public.agents (id) on delete cascade,
  conversation_count integer not null default 0,
  last_met_at timestamptz,
  shared_interests text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (agent_low_id, agent_high_id),
  constraint agent_relationships_normalized_pair check (agent_low_id < agent_high_id),
  constraint agent_relationships_count_nonnegative check (conversation_count >= 0),
  constraint agent_relationships_shared_interests_length check (
    cardinality(shared_interests) <= 20
  )
);

create index agent_relationships_agent_high_id_idx on public.agent_relationships (agent_high_id);

create trigger agent_relationships_set_updated_at
before update on public.agent_relationships
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS: default deny（#6 と同じ原則）
-- ---------------------------------------------------------------------------
alter table public.background_jobs enable row level security;
alter table public.conversations enable row level security;
alter table public.conversation_participants enable row level security;
alter table public.messages enable row level security;
alter table public.usage_records enable row level security;
alter table public.notifications enable row level security;
alter table public.agent_blocks enable row level security;
alter table public.memories enable row level security;
alter table public.agent_relationships enable row level security;

revoke all on table
  public.background_jobs,
  public.conversations,
  public.conversation_participants,
  public.messages,
  public.usage_records,
  public.notifications,
  public.agent_blocks,
  public.memories,
  public.agent_relationships
from anon, authenticated;
