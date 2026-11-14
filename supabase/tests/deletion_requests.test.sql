-- Issue #30（前半）: deletion_requests の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(18);

-- 1. RLS とアクセス拒否（2 + 8）
select ok(
  (select relrowsecurity from pg_class where oid = 'public.deletion_requests'::regclass),
  'deletion_requests: RLS が有効'
);
select is(
  (select count(*)::int from pg_policies where schemaname = 'public' and tablename = 'deletion_requests'),
  0,
  'deletion_requests の RLS ポリシーは 0 件'
);
select throws_ok(
  format('set local role %I; %s', r.role, op.sql),
  '42501',
  null,
  format('%s は deletion_requests へ %s できない', r.role, op.kind)
)
from (values ('anon'), ('authenticated')) as r (role)
cross join (values
  ('SELECT', 'select * from public.deletion_requests'),
  ('INSERT', 'insert into public.deletion_requests (profile_id) values (gen_random_uuid())'),
  ('UPDATE', 'update public.deletion_requests set status = ''failed'''),
  ('DELETE', 'delete from public.deletion_requests')
) as op (kind, sql)
order by r.role, op.kind;

-- 2. 制約（8）
select lives_ok(
  $$insert into public.deletion_requests (profile_id)
    values ('00000000-0000-0000-0000-0000000000d1')$$,
  '削除要求を作れる（初期 pending）'
);
select throws_ok(
  $$insert into public.deletion_requests (profile_id)
    values ('00000000-0000-0000-0000-0000000000d1')$$,
  '23505', null, '進行中の要求は利用者ごとに 1 件'
);
select throws_ok(
  $$update public.deletion_requests set status = 'done'$$,
  '23514', null, 'status は 4 種類だけ'
);
select throws_ok(
  $$update public.deletion_requests set status = 'completed'$$,
  '23514', null, 'completed には完了日時が必要'
);
select throws_ok(
  $$update public.deletion_requests set completed_at = now()$$,
  '23514', null, 'completed 以外は完了日時を持たない'
);
select throws_ok(
  $$update public.deletion_requests set error_code = 'Some error: secret text'$$,
  '23514', null, 'error_code は短い固定コードだけ（例外本文を入れない）'
);
update public.deletion_requests set status = 'completed', completed_at = now()
where profile_id = '00000000-0000-0000-0000-0000000000d1';
select lives_ok(
  $$insert into public.deletion_requests (profile_id)
    values ('00000000-0000-0000-0000-0000000000d1')$$,
  '完了後は新しい要求を作れる（履歴は残る）'
);
-- プロフィール・Auth ユーザーが消えても追跡を残せる（FK なし）
select is(
  (select count(*)::int from pg_constraint
   where conrelid = 'public.deletion_requests'::regclass and contype = 'f'),
  0,
  'profiles への FK を持たない（Auth 削除後も完了を追える）'
);

select * from finish();
rollback;
