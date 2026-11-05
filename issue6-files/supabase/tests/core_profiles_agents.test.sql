-- Issue #6 の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(72);

-- ---------------------------------------------------------------------------
-- 1. RLS: 全対象表で有効、ポリシー 0 件（default deny）
-- ---------------------------------------------------------------------------
select ok(
  c.relrowsecurity,
  format('%s: RLS が有効', c.relname)
)
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in ('profiles', 'agents', 'agent_profiles', 'agent_settings', 'audit_events')
order by c.relname;

select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public'
     and tablename in ('profiles', 'agents', 'agent_profiles', 'agent_settings', 'audit_events')),
  0,
  '対象表の RLS ポリシーは 0 件'
);

-- ---------------------------------------------------------------------------
-- 2. anon / authenticated の直接 SELECT/INSERT/UPDATE/DELETE は拒否 (42501)
--    5 表 x 2 ロール x 4 操作 = 40 件
-- ---------------------------------------------------------------------------
select throws_ok(
  format('set local role %I; %s', r.role, replace(op.sql, '$t', t.name)),
  '42501',
  null,
  format('%s は %s へ %s できない', r.role, t.name, op.kind)
)
from (values ('anon'), ('authenticated')) as r (role)
cross join (values
  ('profiles'), ('agents'), ('agent_profiles'), ('agent_settings'), ('audit_events')
) as t (name)
cross join (values
  ('SELECT', 'select * from public.$t'),
  ('INSERT', 'insert into public.$t default values'),
  ('UPDATE', 'update public.$t set created_at = now()'),
  ('DELETE', 'delete from public.$t')
) as op (kind, sql)
order by r.role, t.name, op.kind;

-- ---------------------------------------------------------------------------
-- テストデータ（このトランザクションの最後に rollback される）
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000000a'),
  ('00000000-0000-0000-0000-00000000000b');

insert into public.profiles (id, display_name) values
  ('00000000-0000-0000-0000-00000000000a', 'Alice'),
  ('00000000-0000-0000-0000-00000000000b', 'Bob');

insert into public.agents (id, owner_id, name) values
  ('10000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-00000000000a', 'AliceAI'),
  ('10000000-0000-0000-0000-00000000000b', '00000000-0000-0000-0000-00000000000b', 'BobAI');

insert into public.agent_profiles (
  agent_id, version, personality, values_text, speaking_style,
  interaction_policy, shareable_info, restrictions, approved_at
) values
  ('10000000-0000-0000-0000-00000000000a', 1, 'p', 'v', 's', 'i', 'sh', 'r', now()),
  ('10000000-0000-0000-0000-00000000000a', 2, 'p', 'v', 's', 'i', 'sh', 'r', null),
  ('10000000-0000-0000-0000-00000000000b', 1, 'p', 'v', 's', 'i', 'sh', 'r', now());

insert into public.agent_settings (agent_id) values ('10000000-0000-0000-0000-00000000000a');

insert into public.audit_events (actor_profile_id, action, resource_type, resource_id)
values ('00000000-0000-0000-0000-00000000000a', 'agent.create', 'agent', '10000000-0000-0000-0000-00000000000a');

-- ---------------------------------------------------------------------------
-- 3. 制約 (FK / UNIQUE / CHECK)
-- ---------------------------------------------------------------------------
-- profiles
select throws_ok(
  $$insert into public.profiles (id, display_name) values (gen_random_uuid(), 'Ghost')$$,
  '23503', null, 'profiles: auth.users にない ID は作れない'
);
select throws_ok(
  $$update public.profiles set display_name = '   ' where display_name = 'Alice'$$,
  '23514', null, 'profiles: 空白だけの表示名は拒否'
);

-- agents
select throws_ok(
  $$insert into public.agents (owner_id, name) values ('00000000-0000-0000-0000-00000000000a', 'Second')$$,
  '23505', null, 'agents: 1 人 2 体目は作れない (UNIQUE owner_id)'
);
select throws_ok(
  $$update public.agents set status = 'unknown' where name = 'AliceAI'$$,
  '23514', null, 'agents: 未定義の status は拒否'
);
select throws_ok(
  $$update public.agents set status = 'active' where name = 'AliceAI'$$,
  '23514', null, 'agents: 未承認のまま active にできない'
);
select throws_ok(
  $$update public.agents set approved_profile_version = 2 where name = 'AliceAI'$$,
  '23514', null, 'agents: 未承認の人格版を承認版として指せない'
);
select throws_ok(
  $$update public.agents set approved_profile_version = 9 where name = 'AliceAI'$$,
  '23514', null, 'agents: 存在しない人格版を指せない'
);
select throws_ok(
  -- 失敗時は subtransaction ごと巻き戻るため、トリガーの無効化も元に戻る
  $$alter table public.agents disable trigger agents_ensure_approved_profile_version;
    update public.agents set approved_profile_version = 9 where name = 'AliceAI'$$,
  '23503', null, 'agents: トリガーがなくても FK が存在しない人格版を拒否する'
);
select lives_ok(
  $$update public.agents set approved_profile_version = 1, status = 'active' where name = 'AliceAI'$$,
  'agents: 承認済み版を指せば active にできる'
);

-- agent_profiles
select throws_ok(
  $$insert into public.agent_profiles (agent_id, version, personality, values_text, speaking_style,
      interaction_policy, shareable_info, restrictions)
    values ('10000000-0000-0000-0000-00000000000a', 1, 'p', 'v', 's', 'i', 'sh', 'r')$$,
  '23505', null, 'agent_profiles: 同じ版番号は重複できない'
);
select throws_ok(
  $$insert into public.agent_profiles (agent_id, version, personality, values_text, speaking_style,
      interaction_policy, shareable_info, restrictions)
    values ('10000000-0000-0000-0000-00000000000a', 0, 'p', 'v', 's', 'i', 'sh', 'r')$$,
  '23514', null, 'agent_profiles: 版番号は 1 以上'
);
select throws_ok(
  $$update public.agent_profiles set private_info_ciphertext = '\x00'
    where agent_id = '10000000-0000-0000-0000-00000000000a' and version = 2$$,
  '23514', null, 'agent_profiles: 暗号文だけで鍵版なしは拒否'
);
select throws_ok(
  $$update public.agent_profiles set personality = repeat('x', 2001)
    where agent_id = '10000000-0000-0000-0000-00000000000a' and version = 2$$,
  '23514', null, 'agent_profiles: 2000 字超は拒否'
);
select throws_ok(
  $$update public.agent_profiles set interests = array_fill('x'::text, array[21])
    where agent_id = '10000000-0000-0000-0000-00000000000a' and version = 2$$,
  '23514', null, 'agent_profiles: 配列 21 件以上は拒否'
);
select throws_ok(
  $$update public.agent_profiles set personality = 'changed'
    where agent_id = '10000000-0000-0000-0000-00000000000a' and version = 1$$,
  '23514', null, 'agent_profiles: 承認済み版は変更できない'
);
select lives_ok(
  $$update public.agent_profiles set personality = 'draft edit', approved_at = now()
    where agent_id = '10000000-0000-0000-0000-00000000000a' and version = 2$$,
  'agent_profiles: 未承認版は編集・承認できる'
);

-- agent_settings
select throws_ok(
  $$update public.agent_settings set frequency_per_day = 3$$,
  '23514', null, 'agent_settings: 頻度は 0〜2'
);
select throws_ok(
  $$update public.agent_settings set daily_budget_usd = -0.01$$,
  '23514', null, 'agent_settings: 予算は 0 以上'
);
select throws_ok(
  $$update public.agent_settings set web_visibility = 'public'$$,
  '23514', null, 'agent_settings: MVP は participants 固定'
);

-- audit_events
select throws_ok(
  $$insert into public.audit_events (action, resource_type, resource_id, metadata)
    values ('x', 'y', 'z', '[]')$$,
  '23514', null, 'audit_events: metadata は JSON オブジェクトのみ'
);

-- ---------------------------------------------------------------------------
-- 4. 削除動作: Auth ユーザー削除 → profiles → agents → 人格版・設定が CASCADE
--    監査は残る
-- ---------------------------------------------------------------------------
delete from auth.users where id = '00000000-0000-0000-0000-00000000000a';

select is(
  (select count(*)::int from public.profiles where id = '00000000-0000-0000-0000-00000000000a'),
  0, '削除: profiles が CASCADE 削除される'
);
select is(
  (select count(*)::int from public.agents where id = '10000000-0000-0000-0000-00000000000a'),
  0, '削除: agents が CASCADE 削除される'
);
select is(
  (select count(*)::int from public.agent_profiles where agent_id = '10000000-0000-0000-0000-00000000000a'),
  0, '削除: agent_profiles が CASCADE 削除される'
);
select is(
  (select count(*)::int from public.agent_settings where agent_id = '10000000-0000-0000-0000-00000000000a'),
  0, '削除: agent_settings が CASCADE 削除される'
);
select is(
  (select count(*)::int from public.agents where id = '10000000-0000-0000-0000-00000000000b'),
  1, '削除: 他人の AI は残る'
);
select is(
  (select count(*)::int from public.audit_events where actor_profile_id = '00000000-0000-0000-0000-00000000000a'),
  1, '削除: 監査記録は残る'
);

select * from finish();
rollback;
