-- Issue #7: 招待の許可リスト（ADR-014）
-- 正本: docs/05-database-design.md「招待テーブルの運用制約」、docs/23-invitation-consent-operations.md
--
-- 方針
-- * 招待は API と運営専用 CLI だけが扱う。RLS 有効・ポリシー 0 件・anon/authenticated の権限なし。
-- * invitations.auth_user_id は Supabase の sub と同値だが、Auth 削除で失効記録が消えないよう
--   auth.users / profiles への FK は張らない。パージはアプリの処理で行う（#30）。
-- * 有効な招待は最大 10 人。Google/GitHub の両方を登録しても 1 人と数える。
--   枠の確認は共通のトランザクションロックの下で行い、同時登録でも 10 人を超えない。
-- * 失効は不可逆。失効済みの招待を有効に戻さず、最初の失効日時も変えない。
-- * 有効な招待は 1 つ以上の許可 identity を持つ（コミット時に検査）。

-- ---------------------------------------------------------------------------
-- invitations: 招待
-- ---------------------------------------------------------------------------
create table public.invitations (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid not null,
  status varchar(16) not null default 'active',
  revoked_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint invitations_auth_user_id_key unique (auth_user_id),
  constraint invitations_status_check check (status in ('active', 'revoked')),
  -- active <=> revoked_at IS NULL
  constraint invitations_status_revoked_at check ((status = 'active') = (revoked_at is null))
);

create index invitations_status_idx on public.invitations (status);

create trigger invitations_set_updated_at
before update on public.invitations
for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- invitation_identities: 許可する (provider, provider_user_id)
-- ---------------------------------------------------------------------------
create table public.invitation_identities (
  id uuid primary key default gen_random_uuid(),
  invitation_id uuid not null references public.invitations (id) on delete cascade,
  provider varchar(16) not null,
  provider_user_id text not null,
  constraint invitation_identities_provider_user_key unique (provider, provider_user_id),
  -- 1 人の招待に同じプロバイダーは 1 つまで。invitation_id の FK 索引を兼ねる
  constraint invitation_identities_invitation_provider_key unique (invitation_id, provider),
  constraint invitation_identities_provider_check check (provider in ('google', 'github')),
  constraint invitation_identities_provider_user_id_check check (
    btrim(provider_user_id) <> ''
    and provider_user_id = btrim(provider_user_id)
    and char_length(provider_user_id) <= 255
  )
);

-- ---------------------------------------------------------------------------
-- 10 人枠
-- ---------------------------------------------------------------------------
-- 登録・再参加の全経路で同じロックを取る（ドメイン処理も先頭でこの関数を呼ぶ）
create function public.lock_invitation_capacity()
returns void
language sql
set search_path = ''
as $$
  select pg_advisory_xact_lock(hashtextextended('alter.invitations.capacity', 0));
$$;

create function public.enforce_invitation_capacity()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  active_count integer;
begin
  if new.status <> 'active' then
    return new;
  end if;
  perform public.lock_invitation_capacity();
  -- ロック取得後の新しいスナップショットで数える（READ COMMITTED）
  select count(*) into active_count
  from public.invitations
  where status = 'active'
    and id <> new.id;
  if active_count >= 10 then
    raise exception 'invitation capacity reached (max 10 active invitations)'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger invitations_enforce_capacity
before insert on public.invitations
for each row execute function public.enforce_invitation_capacity();

-- ---------------------------------------------------------------------------
-- 失効の不可逆性・識別子の不変性
-- ---------------------------------------------------------------------------
create function public.protect_invitation_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.id <> old.id or new.auth_user_id <> old.auth_user_id then
    raise exception 'invitation id and auth_user_id are immutable'
      using errcode = 'check_violation';
  end if;
  if old.status = 'revoked'
     and (new.status <> 'revoked' or new.revoked_at is distinct from old.revoked_at) then
    raise exception 'revoked invitation cannot be reactivated or re-dated'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger invitations_protect_update
before update on public.invitations
for each row execute function public.protect_invitation_update();

create function public.protect_invitation_identity()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' then
    raise exception 'invitation identities are immutable; revoke and re-invite instead'
      using errcode = 'check_violation';
  end if;
  -- INSERT: 失効済みの招待へ identity を追加しない
  if exists (
    select 1 from public.invitations i
    where i.id = new.invitation_id and i.status <> 'active'
  ) then
    raise exception 'cannot add identity to a revoked invitation'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger invitation_identities_protect
before insert or update on public.invitation_identities
for each row execute function public.protect_invitation_identity();

-- ---------------------------------------------------------------------------
-- 有効な招待は 1 つ以上の identity を持つ（コミット時）
-- ---------------------------------------------------------------------------
create function public.ensure_active_invitation_has_identity()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  target_id uuid;
begin
  if tg_table_name = 'invitations' then
    target_id := new.id;
  else
    target_id := old.invitation_id;
  end if;
  if exists (
    select 1 from public.invitations i
    where i.id = target_id
      and i.status = 'active'
      and not exists (
        select 1 from public.invitation_identities ii where ii.invitation_id = i.id
      )
  ) then
    raise exception 'active invitation % must have at least one identity', target_id
      using errcode = 'check_violation';
  end if;
  return null;
end;
$$;

create constraint trigger invitations_require_identity
after insert or update on public.invitations
deferrable initially deferred
for each row execute function public.ensure_active_invitation_has_identity();

create constraint trigger invitation_identities_require_identity
after delete on public.invitation_identities
deferrable initially deferred
for each row execute function public.ensure_active_invitation_has_identity();

-- ---------------------------------------------------------------------------
-- RLS: default deny（#6 と同じ原則）
-- ---------------------------------------------------------------------------
alter table public.invitations enable row level security;
alter table public.invitation_identities enable row level security;

revoke all on table public.invitations, public.invitation_identities from anon, authenticated;

revoke all on function
  public.lock_invitation_capacity(),
  public.enforce_invitation_capacity(),
  public.protect_invitation_update(),
  public.protect_invitation_identity(),
  public.ensure_active_invitation_has_identity()
from public, anon, authenticated;
