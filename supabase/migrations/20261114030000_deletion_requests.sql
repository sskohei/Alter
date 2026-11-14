-- Issue #30（前半）: アカウント削除要求の追跡
-- 正本: docs/05-database-design.md「deletion_requests」、docs/23-invitation-consent-operations.md
--
-- * Auth ユーザー・プロフィールの削除後も「削除が終わったか」を追えるよう、profiles への FK を持たない。
--   行には利用者 ID 以外の個人情報を持たせない（error_code は短い固定コードだけ）。
-- * 進行中（pending / running）の要求は利用者ごとに 1 件（部分 UNIQUE）。
-- * 完了後 30 日で集計して削除する（パージ処理は #30 後半）。
-- * RLS 有効・ポリシー 0 件（default deny）。anon / authenticated の直接アクセスなし。

create table public.deletion_requests (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null,
  status varchar(16) not null default 'pending',
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  error_code text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint deletion_requests_status_check check (
    status in ('pending', 'running', 'completed', 'failed')
  ),
  -- completed のときだけ完了日時を持つ
  constraint deletion_requests_completed_at_check check (
    (status = 'completed') = (completed_at is not null)
  ),
  constraint deletion_requests_time_order check (
    completed_at is null or completed_at >= requested_at
  ),
  constraint deletion_requests_error_code_format check (
    error_code is null or error_code ~ '^[a-z][a-z0-9_.]{0,63}$'
  )
);

create unique index deletion_requests_active_profile_key
  on public.deletion_requests (profile_id) where status in ('pending', 'running');
create index deletion_requests_profile_requested_idx
  on public.deletion_requests (profile_id, requested_at desc);
-- 完了後 30 日の削除・失敗の再試行の対象を引く
create index deletion_requests_status_idx on public.deletion_requests (status, completed_at);

create trigger deletion_requests_set_updated_at
before update on public.deletion_requests
for each row execute function public.set_updated_at();

alter table public.deletion_requests enable row level security;
revoke all on table public.deletion_requests from anon, authenticated;
