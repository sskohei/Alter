-- Issue #30（後半）: アカウント削除のパージと Discord 投稿削除の追跡
-- 正本: docs/05-database-design.md、docs/23-invitation-consent-operations.md「保持・削除」
--
-- discord_cleanup_tasks: 会話を削除する前に、投稿済みスレッドの ID だけを退避する（本文・利用者 ID は持たない）。
-- Bot がスレッドの削除を試し、結果（削除済み・権限不足・対象なし・打ち切り）を残す。完了後 30 日で削除する。
--
-- 戻し方: drop table public.discord_cleanup_tasks;

create table public.discord_cleanup_tasks (
  id uuid primary key default gen_random_uuid(),
  thread_id text not null,
  channel_id text not null,
  status varchar(16) not null default 'pending',
  attempts smallint not null default 0,
  next_attempt_at timestamptz not null default now(),
  last_error text,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- 同じスレッドの削除を二重に積まない（両参加者が削除しても 1 件）
  constraint discord_cleanup_tasks_thread_key unique (thread_id),
  constraint discord_cleanup_tasks_ids_format check (
    public.is_discord_snowflake(thread_id) and public.is_discord_snowflake(channel_id)
  ),
  constraint discord_cleanup_tasks_status_check check (
    status in ('pending', 'sending', 'deleted', 'forbidden', 'not_found', 'failed')
  ),
  -- 終わった状態だけが完了日時を持つ
  constraint discord_cleanup_tasks_completed_check check (
    (status in ('deleted', 'forbidden', 'not_found', 'failed')) = (completed_at is not null)
  ),
  constraint discord_cleanup_tasks_attempts_nonnegative check (attempts >= 0),
  constraint discord_cleanup_tasks_last_error_length check (char_length(last_error) <= 200)
);

create index discord_cleanup_tasks_status_next_attempt_idx
  on public.discord_cleanup_tasks (status, next_attempt_at);
-- 完了後 30 日の削除用
create index discord_cleanup_tasks_completed_at_idx
  on public.discord_cleanup_tasks (completed_at) where completed_at is not null;

create trigger discord_cleanup_tasks_set_updated_at
before update on public.discord_cleanup_tasks
for each row execute function public.set_updated_at();

alter table public.discord_cleanup_tasks enable row level security;
revoke all on table public.discord_cleanup_tasks from anon, authenticated;
