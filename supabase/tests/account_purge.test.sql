-- Issue #30（後半）: discord_cleanup_tasks の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(17);

-- 1. RLS とアクセス拒否（2 + 8）
select ok(
  (select relrowsecurity from pg_class where oid = 'public.discord_cleanup_tasks'::regclass),
  'discord_cleanup_tasks: RLS が有効'
);
select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and tablename = 'discord_cleanup_tasks'),
  0,
  'discord_cleanup_tasks の RLS ポリシーは 0 件'
);
select throws_ok(
  format('set local role %I; %s', r.role, op.sql),
  '42501',
  null,
  format('%s は discord_cleanup_tasks へ %s できない', r.role, op.kind)
)
from (values ('anon'), ('authenticated')) as r (role)
cross join (values
  ('SELECT', 'select * from public.discord_cleanup_tasks'),
  ('INSERT', 'insert into public.discord_cleanup_tasks (thread_id, channel_id) '
             'values (''600000000000000001'', ''300000000000000001'')'),
  ('UPDATE', 'update public.discord_cleanup_tasks set status = ''failed'''),
  ('DELETE', 'delete from public.discord_cleanup_tasks')
) as op (kind, sql)
order by r.role, op.kind;

-- 2. 本文・利用者 ID を持たない（列はスレッド・チャンネルと状態だけ）（1）
select is(
  (select array_agg(column_name::text order by column_name) from information_schema.columns
   where table_schema = 'public' and table_name = 'discord_cleanup_tasks'),
  array['attempts', 'channel_id', 'completed_at', 'created_at', 'id', 'last_error',
        'next_attempt_at', 'status', 'thread_id', 'updated_at'],
  '本文・利用者 ID の列はない'
);

-- 3. 制約（6）
insert into public.discord_cleanup_tasks (thread_id, channel_id)
values ('600000000000000001', '300000000000000001');
select throws_ok(
  $$insert into public.discord_cleanup_tasks (thread_id, channel_id)
    values ('600000000000000001', '300000000000000002')$$,
  '23505', null, '同じスレッドは 1 件だけ'
);
select throws_ok(
  $$insert into public.discord_cleanup_tasks (thread_id, channel_id) values ('abc', '300000000000000001')$$,
  '23514', null, 'スレッド ID は snowflake 形式'
);
select throws_ok(
  $$update public.discord_cleanup_tasks set status = 'unknown'$$,
  '23514', null, '状態は決まった値だけ'
);
select throws_ok(
  $$update public.discord_cleanup_tasks set status = 'deleted'$$,
  '23514', null, '終わった状態には完了日時が要る'
);
select throws_ok(
  $$update public.discord_cleanup_tasks set completed_at = now()$$,
  '23514', null, '未完了の状態は完了日時を持たない'
);
select lives_ok(
  $$update public.discord_cleanup_tasks set status = 'forbidden', completed_at = now()$$,
  '結果と完了日時は一緒に記録できる'
);

select * from finish();
rollback;
