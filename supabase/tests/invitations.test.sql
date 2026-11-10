-- Issue #7: 招待 2 表の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
-- 同時登録の 10 人上限と、失効の原子性は Python の DB 統合テスト（tests/db）で確認する。
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(41);

-- ---------------------------------------------------------------------------
-- 1. RLS: 有効・ポリシー 0 件
-- ---------------------------------------------------------------------------
select ok(
  c.relrowsecurity,
  format('%s: RLS が有効', c.relname)
)
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('invitations', 'invitation_identities')
order by c.relname;

select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and tablename in ('invitations', 'invitation_identities')),
  0,
  '招待 2 表の RLS ポリシーは 0 件'
);

-- ---------------------------------------------------------------------------
-- 2. anon / authenticated の直接 CRUD は拒否（2 表 x 2 ロール x 4 操作 = 16 件）
-- ---------------------------------------------------------------------------
select throws_ok(
  format('set local role %I; %s', r.role, replace(op.sql, '$t', t.name)),
  '42501',
  null,
  format('%s は %s へ %s できない', r.role, t.name, op.kind)
)
from (values ('anon'), ('authenticated')) as r (role)
cross join (values ('invitations'), ('invitation_identities')) as t (name)
cross join (values
  ('SELECT', 'select * from public.$t'),
  ('INSERT', 'insert into public.$t default values'),
  ('UPDATE', 'update public.$t set id = id'),
  ('DELETE', 'delete from public.$t')
) as op (kind, sql)
order by r.role, t.name, op.kind;

-- ---------------------------------------------------------------------------
-- テストデータ: 有効な招待 A（Google + GitHub）、有効な招待 B（Google）
-- ---------------------------------------------------------------------------
insert into public.invitations (id, auth_user_id) values
  ('a0000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1'),
  ('b0000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000b1');
insert into public.invitation_identities (invitation_id, provider, provider_user_id) values
  ('a0000000-0000-0000-0000-000000000001', 'google', 'google-a'),
  ('a0000000-0000-0000-0000-000000000001', 'github', '1001'),
  ('b0000000-0000-0000-0000-000000000001', 'google', 'google-b');
set constraints all immediate;
set constraints all deferred;

-- ---------------------------------------------------------------------------
-- 3. 一意性・CHECK
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.invitations (auth_user_id) values ('00000000-0000-0000-0000-0000000000a1')$$,
  '23505', null, '同じ sub の招待は 2 つ作れない'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('b0000000-0000-0000-0000-000000000001', 'github', '1001')$$,
  '23505', null, '同じ provider ID を別の招待に登録できない'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('a0000000-0000-0000-0000-000000000001', 'google', 'google-a2')$$,
  '23505', null, '1 つの招待に同じプロバイダーは 1 つまで'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('b0000000-0000-0000-0000-000000000001', 'email', 'b@example.com')$$,
  '23514', null, 'google / github 以外のプロバイダーは拒否'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('b0000000-0000-0000-0000-000000000001', 'github', '   ')$$,
  '23514', null, '空白だけの provider ID は拒否'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('b0000000-0000-0000-0000-000000000001', 'github', ' 2002')$$,
  '23514', null, '前後に空白のある provider ID は拒否'
);
select throws_ok(
  $$insert into public.invitations (auth_user_id, status) values (gen_random_uuid(), 'pending')$$,
  '23514', null, '未定義の status は拒否'
);
select throws_ok(
  $$update public.invitations set revoked_at = now()
    where id = 'b0000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'active のまま revoked_at を入れられない'
);
select throws_ok(
  $$update public.invitation_identities set provider_user_id = 'changed'
    where provider_user_id = 'google-b'$$,
  '23514', null, 'identity は書き換えられない'
);
select throws_ok(
  $$update public.invitations set auth_user_id = gen_random_uuid()
    where id = 'b0000000-0000-0000-0000-000000000001'$$,
  '23514', null, '招待の sub は書き換えられない'
);

-- ---------------------------------------------------------------------------
-- 4. 有効な招待は identity 必須（コミット時に検査）
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.invitations (auth_user_id) values (gen_random_uuid());
    set constraints all immediate$$,
  '23514', null, 'identity のない有効な招待はコミットできない'
);
select throws_ok(
  $$delete from public.invitation_identities where provider_user_id = 'google-b';
    set constraints all immediate$$,
  '23514', null, '有効な招待の最後の identity は消せない'
);
select lives_ok(
  $$delete from public.invitation_identities where provider_user_id = '1001';
    set constraints all immediate$$,
  '他の identity が残るなら 1 つ消せる'
);
-- lives_ok 内の SET CONSTRAINTS はトランザクション全体に残るので戻す
set constraints all deferred;

-- ---------------------------------------------------------------------------
-- 5. 失効は不可逆
-- ---------------------------------------------------------------------------
update public.invitations
set status = 'revoked', revoked_at = '2026-11-08 00:00:00+00'
where id = 'b0000000-0000-0000-0000-000000000001';

select throws_ok(
  $$update public.invitations set status = 'active', revoked_at = null
    where id = 'b0000000-0000-0000-0000-000000000001'$$,
  '23514', null, '失効済みの招待は有効に戻せない'
);
select throws_ok(
  $$update public.invitations set revoked_at = now()
    where id = 'b0000000-0000-0000-0000-000000000001'$$,
  '23514', null, '最初の失効日時は変えられない'
);
select throws_ok(
  $$insert into public.invitation_identities (invitation_id, provider, provider_user_id)
    values ('b0000000-0000-0000-0000-000000000001', 'github', '2002')$$,
  '23514', null, '失効済みの招待に identity を追加できない'
);
select lives_ok(
  $$delete from public.invitation_identities where provider_user_id = 'google-b';
    set constraints all immediate$$,
  '失効済みの招待の identity はパージできる'
);
-- lives_ok 内の SET CONSTRAINTS はトランザクション全体に残るので戻す
set constraints all deferred;

-- ---------------------------------------------------------------------------
-- 6. 10 人枠（失効済みは数えない）
--    有効: A のみ（1 人）→ 9 人追加で 10 人、11 人目は拒否
-- ---------------------------------------------------------------------------
create temporary table extra_invites as
  select gen_random_uuid() as id, gen_random_uuid() as sub from generate_series(1, 9);

insert into public.invitations (id, auth_user_id) select id, sub from extra_invites;
insert into public.invitation_identities (invitation_id, provider, provider_user_id)
  select id, 'google', 'g-' || id from extra_invites;

select is(
  (select count(*)::int from public.invitations where status = 'active'),
  10, '有効な招待は 10 人'
);
select throws_ok(
  $$insert into public.invitations (auth_user_id) values (gen_random_uuid())$$,
  '23514', null, '11 人目は登録できない'
);

-- 1 人失効すると枠が空く
update public.invitations
set status = 'revoked', revoked_at = now()
where id = (select id from extra_invites limit 1);

select lives_ok(
  $$insert into public.invitations (id, auth_user_id)
      values ('c0000000-0000-0000-0000-000000000001', gen_random_uuid());
    insert into public.invitation_identities (invitation_id, provider, provider_user_id)
      values ('c0000000-0000-0000-0000-000000000001', 'github', '3003');
    set constraints all immediate$$,
  '失効で空いた枠に登録できる'
);
-- lives_ok 内の SET CONSTRAINTS はトランザクション全体に残るので戻す
set constraints all deferred;

-- ---------------------------------------------------------------------------
-- 7. 削除（パージ）: 招待を消すと identity も消える。Auth/プロフィールには依存しない
-- ---------------------------------------------------------------------------
delete from public.invitations where id = 'a0000000-0000-0000-0000-000000000001';

select is(
  (select count(*)::int from public.invitation_identities
   where invitation_id = 'a0000000-0000-0000-0000-000000000001'),
  0, '招待の削除で identity が CASCADE 削除される'
);
select is(
  (select count(*)::int from information_schema.table_constraints tc
   join information_schema.constraint_column_usage ccu using (constraint_name, constraint_schema)
   where tc.table_schema = 'public' and tc.table_name = 'invitations'
     and tc.constraint_type = 'FOREIGN KEY'),
  0, 'invitations は auth.users / profiles に FK を張らない'
);

select * from finish();
rollback;
