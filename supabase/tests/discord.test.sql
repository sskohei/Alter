-- Issue #26: Discord 連携・ギルド・同意・配信 outbox の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(99);

create temporary table _tables (name text primary key) on commit drop;
insert into _tables values
  ('discord_connections'), ('discord_oauth_states'), ('discord_guilds'), ('discord_channels'),
  ('agent_discord_consents'), ('discord_deliveries'), ('discord_dm_deliveries');

-- ---------------------------------------------------------------------------
-- 1. RLS: 7 表で有効、ポリシー 0 件（7 + 1）
-- ---------------------------------------------------------------------------
select ok(c.relrowsecurity, format('%s: RLS が有効', c.relname))
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
join _tables t on t.name = c.relname
where n.nspname = 'public'
order by c.relname;

select is(
  (select count(*)::int from pg_policies p join _tables t on t.name = p.tablename
   where p.schemaname = 'public'),
  0,
  '7 表の RLS ポリシーは 0 件'
);

-- ---------------------------------------------------------------------------
-- 2. anon / authenticated の直接 CRUD は拒否（7 表 x 2 ロール x 4 操作 = 56 件）
-- ---------------------------------------------------------------------------
select throws_ok(
  format('set local role %I; %s', r.role, replace(op.sql, '$t', t.name)),
  '42501',
  null,
  format('%s は %s へ %s できない', r.role, t.name, op.kind)
)
from (values ('anon'), ('authenticated')) as r (role)
cross join _tables t
cross join (values
  ('SELECT', 'select * from public.$t'),
  ('INSERT', 'insert into public.$t default values'),
  ('UPDATE', 'update public.$t set created_at = now()'),
  ('DELETE', 'delete from public.$t')
) as op (kind, sql)
order by r.role, t.name, op.kind;

-- ---------------------------------------------------------------------------
-- 3. public の全業務テーブルで RLS 有効・ポリシー 0 件（後から足した表も漏らさない）（2）
-- ---------------------------------------------------------------------------
select is(
  (select coalesce(array_agg(c.relname::text order by c.relname), '{}')
   from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity),
  '{}'::text[],
  'public の全テーブルで RLS が有効'
);
select is(
  (select count(*)::int from pg_policies where schemaname = 'public'),
  0,
  'public の RLS ポリシーは 0 件（default deny）'
);

-- ---------------------------------------------------------------------------
-- 4. トークンを保存する列がない（1）
-- ---------------------------------------------------------------------------
select is(
  (select count(*)::int from information_schema.columns c join _tables t on t.name = c.table_name
   where c.table_schema = 'public' and (c.column_name ilike '%token%' or c.column_name ilike '%secret%')),
  0,
  'Discord のトークン・シークレットを保存する列はない'
);

-- ---------------------------------------------------------------------------
-- テストデータ
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000000a'),
  ('00000000-0000-0000-0000-00000000000b');
insert into public.profiles (id, display_name) values
  ('00000000-0000-0000-0000-00000000000a', 'A'),
  ('00000000-0000-0000-0000-00000000000b', 'B');
insert into public.agents (id, owner_id, name) values
  ('10000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-00000000000a', 'AA'),
  ('10000000-0000-0000-0000-00000000000b', '00000000-0000-0000-0000-00000000000b', 'BB');

-- ---------------------------------------------------------------------------
-- 5. discord_connections: 形式・有効行の一意性（7）
-- ---------------------------------------------------------------------------
select lives_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000a', '100000000000000001')$$,
  '連携を作れる'
);
select throws_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000b', 'abc')$$,
  '23514', null, 'snowflake 以外の Discord ID は拒否'
);
select throws_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000a', '100000000000000002')$$,
  '23505', null, '有効な連携は利用者ごとに 1 件'
);
select throws_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000b', '100000000000000001')$$,
  '23505', null, '有効な連携は Discord ユーザーごとに 1 件'
);
update public.discord_connections set revoked_at = now()
where profile_id = '00000000-0000-0000-0000-00000000000a';
select lives_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000b', '100000000000000001')$$,
  '失効した Discord ユーザーは別の利用者と連携できる'
);
select lives_ok(
  $$insert into public.discord_connections (profile_id, discord_user_id)
    values ('00000000-0000-0000-0000-00000000000a', '100000000000000003')$$,
  '失効後は同じ利用者が再連携できる（履歴は残る）'
);
select throws_ok(
  $$update public.discord_connections set revoked_at = connected_at - interval '1 second'
    where discord_user_id = '100000000000000003'$$,
  '23514', null, '失効日時は連携日時より前にできない'
);

-- ---------------------------------------------------------------------------
-- 6. discord_oauth_states: ハッシュ形式・一意・10 分以内（5）
-- ---------------------------------------------------------------------------
select lives_ok(
  format(
    $$insert into public.discord_oauth_states (profile_id, state_hash, expires_at)
      values ('00000000-0000-0000-0000-00000000000a', %L, now() + interval '10 minutes')$$,
    repeat('a', 64)
  ),
  'state のハッシュを保存できる'
);
select throws_ok(
  format(
    $$insert into public.discord_oauth_states (profile_id, state_hash, expires_at)
      values ('00000000-0000-0000-0000-00000000000b', %L, now() + interval '5 minutes')$$,
    repeat('a', 64)
  ),
  '23505', null, '同じ state は二重に作れない'
);
select throws_ok(
  $$insert into public.discord_oauth_states (profile_id, state_hash, expires_at)
    values ('00000000-0000-0000-0000-00000000000a', 'plain-state-value', now() + interval '5 minutes')$$,
  '23514', null, '平文の state は保存できない（SHA-256 の 16 進だけ）'
);
select throws_ok(
  format(
    $$insert into public.discord_oauth_states (profile_id, state_hash, expires_at)
      values ('00000000-0000-0000-0000-00000000000a', %L, now() + interval '11 minutes')$$,
    repeat('b', 64)
  ),
  '23514', null, '有効期間は 10 分まで'
);
select throws_ok(
  format(
    $$insert into public.discord_oauth_states (profile_id, state_hash, expires_at)
      values ('00000000-0000-0000-0000-00000000000a', %L, now() - interval '1 minute')$$,
    repeat('c', 64)
  ),
  '23514', null, '最初から期限切れの state は作れない'
);

-- ---------------------------------------------------------------------------
-- 7. ギルド・チャンネル・同意（8）
-- ---------------------------------------------------------------------------
select lives_ok(
  $$insert into public.discord_guilds (id, name, admin_discord_user_id)
    values ('200000000000000001', 'G', '400000000000000001')$$,
  'ギルドを登録できる'
);
select throws_ok(
  $$insert into public.discord_guilds (id, admin_discord_user_id) values ('x', '400000000000000001')$$,
  '23514', null, 'ギルド ID は snowflake'
);
select lives_ok(
  $$insert into public.discord_channels (id, guild_id, configured_by)
    values ('300000000000000001', '200000000000000001', '400000000000000001')$$,
  '投稿先を設定できる'
);
select throws_ok(
  $$insert into public.discord_channels (id, guild_id, configured_by)
    values ('300000000000000002', '200000000000000001', '400000000000000001')$$,
  '23505', null, '有効な投稿先は 1 ギルド 1 つ'
);
update public.discord_channels set enabled = false where id = '300000000000000001';
select lives_ok(
  $$insert into public.discord_channels (id, guild_id, configured_by)
    values ('300000000000000002', '200000000000000001', '400000000000000001')$$,
  '無効にすれば別の投稿先を設定できる'
);
select lives_ok(
  $$insert into public.agent_discord_consents (agent_id, guild_id)
    values ('10000000-0000-0000-0000-00000000000a', '200000000000000001'),
           ('10000000-0000-0000-0000-00000000000b', '200000000000000001')$$,
  'AI ごとのギルド別同意を記録できる'
);
select throws_ok(
  $$insert into public.agent_discord_consents (agent_id, guild_id)
    values ('10000000-0000-0000-0000-00000000000a', '200000000000000001')$$,
  '23505', null, '同意は AI × ギルドで 1 件'
);
select throws_ok(
  $$insert into public.agent_discord_consents (agent_id, guild_id)
    values ('10000000-0000-0000-0000-00000000000a', '299999999999999999')$$,
  '23503', null, '未登録のギルドへの同意は作れない'
);

-- ---------------------------------------------------------------------------
-- 8. 配信 outbox（9）
-- ---------------------------------------------------------------------------
insert into public.conversations (id, status) values
  ('20000000-0000-0000-0000-000000000001', 'completed');
insert into public.conversation_participants
  (conversation_id, agent_id, ordinal, discord_consent, web_consent) values
  ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 1, true, true),
  ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000b', 2, true, true);
insert into public.notifications (id, profile_id, kind, dedupe_key, conversation_id) values
  ('30000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-00000000000a',
   'conversation.completed', 'k1', '20000000-0000-0000-0000-000000000001');

select lives_ok(
  $$insert into public.discord_deliveries (conversation_id, channel_id)
    values ('20000000-0000-0000-0000-000000000001', '300000000000000002')$$,
  '投稿 outbox を作れる（初期 pending）'
);
select throws_ok(
  $$insert into public.discord_deliveries (conversation_id, channel_id)
    values ('20000000-0000-0000-0000-000000000001', '300000000000000002')$$,
  '23505', null, '同じ会話を同じ投稿先へ二重に積めない'
);
select throws_ok(
  $$update public.discord_deliveries set status = 'posted'$$,
  '23514', null, '投稿 outbox の status は 5 種類だけ'
);
select throws_ok(
  $$delete from public.discord_channels where id = '300000000000000002'$$,
  '23503', null, '配信履歴がある投稿先は削除できない（RESTRICT）'
);
select lives_ok(
  $$insert into public.discord_dm_deliveries (notification_id, discord_user_id)
    values ('30000000-0000-0000-0000-000000000001', '100000000000000003')$$,
  'DM outbox を作れる'
);
select throws_ok(
  $$insert into public.discord_dm_deliveries (notification_id, discord_user_id)
    values ('30000000-0000-0000-0000-000000000001', '100000000000000003')$$,
  '23505', null, '通知 1 件に DM は 1 件'
);
select throws_ok(
  $$update public.discord_dm_deliveries set status = 'delivered'$$,
  '23514', null, 'DM outbox の status は 5 種類だけ'
);
delete from public.conversations where id = '20000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.discord_deliveries),
  0,
  '会話を消すと投稿 outbox も消える（CASCADE）'
);
select is(
  (select count(*)::int from public.discord_dm_deliveries),
  0,
  '通知（会話経由）を消すと DM outbox も消える（CASCADE）'
);

-- ---------------------------------------------------------------------------
-- 9. 削除の連鎖（3）
-- ---------------------------------------------------------------------------
delete from public.agents where id = '10000000-0000-0000-0000-00000000000b';
select is(
  (select count(*)::int from public.agent_discord_consents
   where agent_id = '10000000-0000-0000-0000-00000000000b'),
  0,
  'AI を消すとギルド別同意も消える'
);
delete from auth.users where id = '00000000-0000-0000-0000-00000000000a';
select is(
  (select count(*)::int from public.discord_connections
   where profile_id = '00000000-0000-0000-0000-00000000000a')
  + (select count(*)::int from public.discord_oauth_states
     where profile_id = '00000000-0000-0000-0000-00000000000a'),
  0,
  '利用者を消すと連携と state も消える'
);
delete from public.discord_guilds where id = '200000000000000001';
select is(
  (select count(*)::int from public.discord_channels where guild_id = '200000000000000001'),
  0,
  'ギルドを消すと投稿先も消える（配信履歴がない場合）'
);

select * from finish();
rollback;
