-- Issue #13: 会話・費用・ジョブ・記憶/関係性の受け入れテスト（pgTAP）
-- 実行: pnpm exec supabase test db
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(138);

create temporary table _tables (name text primary key) on commit drop;
insert into _tables values
  ('background_jobs'), ('conversations'), ('conversation_participants'), ('messages'),
  ('usage_records'), ('notifications'), ('agent_blocks'), ('memories'), ('agent_relationships');

-- ---------------------------------------------------------------------------
-- 1. RLS: 9 表で有効、ポリシー 0 件
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
  '9 表の RLS ポリシーは 0 件'
);

-- ---------------------------------------------------------------------------
-- 2. anon / authenticated の直接 CRUD は拒否（9 表 x 2 ロール x 4 操作 = 72 件）
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
-- テストデータ: 利用者 A / B / C と各 AI、A と B の会話 1 件
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000000a'),
  ('00000000-0000-0000-0000-00000000000b'),
  ('00000000-0000-0000-0000-00000000000c');
insert into public.profiles (id, display_name) values
  ('00000000-0000-0000-0000-00000000000a', 'A'),
  ('00000000-0000-0000-0000-00000000000b', 'B'),
  ('00000000-0000-0000-0000-00000000000c', 'C');
insert into public.agents (id, owner_id, name) values
  ('10000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-00000000000a', 'AI-A'),
  ('10000000-0000-0000-0000-00000000000b', '00000000-0000-0000-0000-00000000000b', 'AI-B'),
  ('10000000-0000-0000-0000-00000000000c', '00000000-0000-0000-0000-00000000000c', 'AI-C');

insert into public.background_jobs (id, kind, dedupe_key, payload) values
  ('20000000-0000-0000-0000-000000000001', 'conversation.run',
   '2026-11-09:1:10000000-0000-0000-0000-00000000000a:10000000-0000-0000-0000-00000000000b',
   '{"slot": 1}');
insert into public.conversations (id, job_id, profile_versions) values
  ('30000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001',
   '{"10000000-0000-0000-0000-00000000000a": 1, "10000000-0000-0000-0000-00000000000b": 1}');
insert into public.conversation_participants
  (conversation_id, agent_id, ordinal, discord_consent, web_consent) values
  ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 1, false, true),
  ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000b', 2, false, true);
insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status) values
  ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 1, 'こんにちは', 'allowed'),
  ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000b', 2, 'はじめまして', 'allowed');
insert into public.usage_records
  (agent_id, conversation_id, provider, model, estimated_usd, kind, idempotency_key) values
  ('10000000-0000-0000-0000-00000000000a', '30000000-0000-0000-0000-000000000001',
   'mock', 'mock-1', 0.05, 'reserve', 'reserve:30000000:a');
insert into public.notifications (profile_id, kind, conversation_id, dedupe_key) values
  ('00000000-0000-0000-0000-00000000000a', 'conversation_completed',
   '30000000-0000-0000-0000-000000000001', 'completed:30000000-0000-0000-0000-000000000001');
insert into public.memories (agent_id, source_conversation_id, text, importance, dedupe_key) values
  ('10000000-0000-0000-0000-00000000000a', '30000000-0000-0000-0000-000000000001',
   'B さんは登山が好きと話していた', 3, 'hash-1'),
  ('10000000-0000-0000-0000-00000000000a', null, '出典のない記憶', 2, 'hash-2');
insert into public.agent_relationships (agent_low_id, agent_high_id, conversation_count) values
  ('10000000-0000-0000-0000-00000000000a', '10000000-0000-0000-0000-00000000000b', 1);
insert into public.agent_blocks (blocker_agent_id, blocked_agent_id) values
  ('10000000-0000-0000-0000-00000000000a', '10000000-0000-0000-0000-00000000000c');

-- ---------------------------------------------------------------------------
-- 3. background_jobs
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.background_jobs (kind, dedupe_key)
    values ('conversation.run', '2026-11-09:1:10000000-0000-0000-0000-00000000000a:10000000-0000-0000-0000-00000000000b')$$,
  '23505', null, 'jobs: 同じ (kind, dedupe_key) は予約できない'
);
select lives_ok(
  $$insert into public.background_jobs (kind, dedupe_key)
    values ('discord.delete', '2026-11-09:1:10000000-0000-0000-0000-00000000000a:10000000-0000-0000-0000-00000000000b')$$,
  'jobs: kind が違えば同じ dedupe_key でもよい'
);
select throws_ok(
  $$update public.background_jobs set status = 'done' where id = '20000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'jobs: 未定義の status は拒否'
);
select throws_ok(
  $$update public.background_jobs set status = 'running' where id = '20000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'jobs: lease なしで running にできない'
);
select throws_ok(
  $$update public.background_jobs set lease_until = now() + interval '90 seconds'
    where id = '20000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'jobs: queued のまま lease を持てない'
);
select lives_ok(
  $$update public.background_jobs
    set status = 'running', lease_until = now() + interval '90 seconds', attempts = attempts + 1
    where id = '20000000-0000-0000-0000-000000000001' and status = 'queued'$$,
  'jobs: 条件付き UPDATE で lease を取得できる'
);
select throws_ok(
  $$insert into public.background_jobs (kind, dedupe_key) values ('Conversation Run', 'x')$$,
  '23514', null, 'jobs: kind の形式（小文字・数字・_.）'
);
select throws_ok(
  $$insert into public.background_jobs (kind, dedupe_key, payload) values ('conversation.run', 'y', '[]')$$,
  '23514', null, 'jobs: payload は JSON オブジェクトのみ'
);
select throws_ok(
  $$insert into public.background_jobs (kind, dedupe_key, attempts) values ('conversation.run', 'z', -1)$$,
  '23514', null, 'jobs: attempts は 0 以上'
);

-- ---------------------------------------------------------------------------
-- 4. conversations / participants
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.conversations (job_id) values ('20000000-0000-0000-0000-000000000001')$$,
  '23505', null, 'conversations: 1 ジョブに会話は 1 つ'
);
select throws_ok(
  $$update public.conversations set status = 'done' where id = '30000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'conversations: 未定義の status は拒否'
);
select throws_ok(
  $$update public.conversations set visibility = 'public' where id = '30000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'conversations: MVP は participants 固定'
);
select throws_ok(
  $$update public.conversations set started_at = now(), ended_at = now() - interval '1 minute'
    where id = '30000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'conversations: 終了時刻は開始時刻より後'
);
select throws_ok(
  $$update public.conversations set cost_usd = -0.01 where id = '30000000-0000-0000-0000-000000000001'$$,
  '23514', null, 'conversations: 費用は 0 以上'
);
select throws_ok(
  $$insert into public.conversation_participants
      (conversation_id, agent_id, ordinal, discord_consent, web_consent)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000c', 2, false, true)$$,
  '23505', null, 'participants: 同じ ordinal は 1 会話に 1 つ'
);
select throws_ok(
  $$insert into public.conversation_participants
      (conversation_id, agent_id, ordinal, discord_consent, web_consent)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000c', 3, false, true)$$,
  '23514', null, 'participants: ordinal は 1 か 2'
);

-- ---------------------------------------------------------------------------
-- 5. messages
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 2, 'dup', 'allowed')$$,
  '23505', null, 'messages: 同じ turn_no は保存できない（冪等キー）'
);
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 9, '9th', 'allowed')$$,
  '23514', null, 'messages: 9 発言目は保存できない'
);
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 0, 'zero', 'allowed')$$,
  '23514', null, 'messages: turn_no は 1 から'
);
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000c', 3, '割り込み', 'allowed')$$,
  '23503', null, 'messages: 参加者でない AI は発言できない'
);
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 3, '   ', 'allowed')$$,
  '23514', null, 'messages: 空白だけの発言は拒否'
);
select throws_ok(
  $$insert into public.messages (conversation_id, agent_id, turn_no, content, moderation_status)
    values ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-00000000000a', 3, 'x', 'unknown')$$,
  '23514', null, 'messages: 未定義の moderation_status は拒否'
);

-- ---------------------------------------------------------------------------
-- 6. usage_records / notifications
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.usage_records (agent_id, provider, model, estimated_usd, kind, idempotency_key)
    values ('10000000-0000-0000-0000-00000000000a', 'mock', 'mock-1', 0.01, 'charge', 'reserve:30000000:a')$$,
  '23505', null, 'usage: 同じ idempotency_key は記録できない'
);
select throws_ok(
  $$insert into public.usage_records (agent_id, provider, model, estimated_usd, kind, idempotency_key)
    values ('10000000-0000-0000-0000-00000000000a', 'mock', 'mock-1', 0.01, 'refund', 'k-1')$$,
  '23514', null, 'usage: kind は reserve / charge / release'
);
select throws_ok(
  $$insert into public.usage_records (agent_id, provider, model, estimated_usd, kind, idempotency_key)
    values ('10000000-0000-0000-0000-00000000000a', 'mock', 'mock-1', -0.01, 'charge', 'k-2')$$,
  '23514', null, 'usage: 金額は 0 以上'
);
select throws_ok(
  $$insert into public.usage_records (provider, model, estimated_usd, kind, idempotency_key)
    values ('mock', 'mock-1', 0, 'charge', 'no-agent')$$,
  '23502', null, 'usage: 新しい記録は AI の指定が必須'
);
select throws_ok(
  $$insert into public.notifications (profile_id, kind, dedupe_key)
    values ('00000000-0000-0000-0000-00000000000a', 'conversation_completed',
            'completed:30000000-0000-0000-0000-000000000001')$$,
  '23505', null, 'notifications: 同じ通知は 1 人 1 件'
);
select throws_ok(
  $$insert into public.notifications (profile_id, kind, dedupe_key, body)
    values ('00000000-0000-0000-0000-00000000000a', 'budget_paused', 'b-1', '[]')$$,
  '23514', null, 'notifications: body は JSON オブジェクトのみ'
);

-- ---------------------------------------------------------------------------
-- 7. agent_blocks / memories / agent_relationships
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.agent_blocks values
    ('10000000-0000-0000-0000-00000000000b', '10000000-0000-0000-0000-00000000000b')$$,
  '23514', null, 'blocks: 自分自身はブロックできない'
);
select throws_ok(
  $$insert into public.memories (agent_id, text, importance, dedupe_key)
    values ('10000000-0000-0000-0000-00000000000a', '重複', 3, 'hash-1')$$,
  '23505', null, 'memories: 同じ候補（dedupe_key）は棄却'
);
select throws_ok(
  $$insert into public.memories (agent_id, text, importance, dedupe_key)
    values ('10000000-0000-0000-0000-00000000000a', '重要度 6', 6, 'hash-6')$$,
  '23514', null, 'memories: 重要度は 1〜5'
);
select throws_ok(
  $$insert into public.memories (agent_id, text, importance, dedupe_key, status)
    values ('10000000-0000-0000-0000-00000000000a', '状態', 3, 'hash-7', 'deleted')$$,
  '23514', null, 'memories: 未定義の status は拒否'
);
select throws_ok(
  $$insert into public.agent_relationships (agent_low_id, agent_high_id)
    values ('10000000-0000-0000-0000-00000000000b', '10000000-0000-0000-0000-00000000000a')$$,
  '23514', null, 'relationships: ID は昇順に正規化（low < high）'
);
select throws_ok(
  $$update public.agent_relationships set conversation_count = -1$$,
  '23514', null, 'relationships: 回数は 0 以上'
);

-- ---------------------------------------------------------------------------
-- 8. 索引（Scheduler / Worker が使う）
-- ---------------------------------------------------------------------------
select has_index('public', 'background_jobs', 'background_jobs_waiting_idx', 'jobs: 待機ジョブ取得の索引');
select has_index('public', 'background_jobs', 'background_jobs_lease_idx', 'jobs: lease 切れ再取得の索引');
select has_index('public', 'usage_records', 'usage_records_agent_created_at_idx', 'usage: 日次上限判定の索引');
select has_index('public', 'usage_records', 'usage_records_created_at_idx', 'usage: 運営全体の日次集計・期限削除の索引');
select has_index('public', 'conversation_participants', 'conversation_participants_agent_id_idx', 'participants: AI ごとの会話の索引');
select has_index('public', 'memories', 'memories_agent_status_importance_idx', 'memories: 記憶検索の索引');
select is(
  (select count(*)::int
   from pg_constraint con
   join pg_class rel on rel.oid = con.conrelid
   join _tables t on t.name = rel.relname
   where con.contype = 'f'
     and not exists (
       select 1 from pg_index i
       where i.indrelid = con.conrelid
         and (i.indkey::int2[])[0:cardinality(con.conkey) - 1] = con.conkey
     )),
  0,
  'すべての FK に先頭一致の索引がある'
);

-- ---------------------------------------------------------------------------
-- 9. 削除動作
-- ---------------------------------------------------------------------------
select throws_ok(
  $$delete from public.agents where id = '10000000-0000-0000-0000-00000000000a'$$,
  '23503', null, '削除: 会話が残っている AI は消せない（参加者 RESTRICT）'
);

-- ジョブ（30〜90 日）を先に消しても会話（90 日）は残る
delete from public.background_jobs where id = '20000000-0000-0000-0000-000000000001';
select is(
  (select job_id from public.conversations where id = '30000000-0000-0000-0000-000000000001'),
  null, '削除: ジョブを消すと会話の job_id は NULL になる'
);

delete from public.conversations where id = '30000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.conversation_participants), 0, '削除: 会話を消すと参加者も消える');
select is((select count(*)::int from public.messages), 0, '削除: 会話を消すと発言も消える');
select is((select count(*)::int from public.notifications), 0, '削除: 会話を消すと関連通知も消える');
select is(
  (select array_agg(dedupe_key)::text from public.memories), '{hash-2}',
  '削除: 会話を消すと出典の記憶だけ消える'
);
select is(
  (select conversation_id from public.usage_records where idempotency_key = 'reserve:30000000:a'),
  null, '削除: 費用台帳（12 か月）は残り、会話との紐付けだけ外れる'
);

-- 会話がなくなれば AI を消せる。記憶・関係・ブロックは CASCADE、費用台帳は匿名化して残す
-- 匿名化で会話との紐付けも外れることを確かめるため、まだ残っている会話を指す記録を足す
insert into public.conversations (id) values ('30000000-0000-0000-0000-000000000002');
insert into public.usage_records
  (agent_id, conversation_id, provider, model, estimated_usd, actual_usd, kind, idempotency_key) values
  ('10000000-0000-0000-0000-00000000000a', '30000000-0000-0000-0000-000000000002',
   'mock', 'mock-1', 0.01, 0.008, 'charge', 'charge:30000000-0000-0000-0000-000000000002:a');

delete from public.agents where id = '10000000-0000-0000-0000-00000000000a';
select is((select count(*)::int from public.memories), 0, '削除: AI を消すと記憶も消える');
select is((select count(*)::int from public.usage_records), 2, '削除: AI を消しても費用台帳は残る（12 か月保持）');
select is(
  (select count(*)::int from public.usage_records where agent_id is not null or conversation_id is not null),
  0, '削除: 残った費用台帳から AI・会話への紐付けが外れる'
);
select is(
  (select count(*)::int from public.usage_records
   where idempotency_key <> 'anonymized:' || id::text),
  0, '削除: ID を含み得る冪等キーも匿名の値に置き換わる'
);
select is(
  (select sum(estimated_usd)::text || '/' || sum(actual_usd)::text from public.usage_records),
  '0.060000/0.008000', '削除: 金額は残り、運営全体の日次集計が減らない'
);
select throws_ok(
  $$update public.usage_records set agent_id = '10000000-0000-0000-0000-00000000000b'$$,
  '23514', null, '削除: 匿名化した費用台帳を別の AI に付け直せない'
);
select is((select count(*)::int from public.agent_relationships), 0, '削除: AI を消すと関係も消える');
select is((select count(*)::int from public.agent_blocks), 0, '削除: AI を消すとブロックも消える');

select * from finish();
rollback;
