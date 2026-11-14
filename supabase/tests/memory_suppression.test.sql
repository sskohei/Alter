-- #22: suppression/progress records are server-only and follow source/agent deletion.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(81);

create temporary table _memory_tables (name text primary key) on commit drop;
insert into _memory_tables values
  ('memory_tombstones'), ('memory_source_suppressions'), ('memory_completions');

select ok(c.relrowsecurity, format('%s: RLS enabled', t.name))
from _memory_tables t
join pg_class c on c.oid = format('public.%I', t.name)::regclass
order by t.name;
select is(
  (select count(*)::int from pg_policies p
   where p.schemaname = 'public' and p.tablename = t.name),
  0, format('%s: no policies', t.name)
)
from _memory_tables t order by t.name;

-- Verify all table privileges, including those outside ordinary CRUD.
select ok(
  not has_table_privilege(r.role, format('public.%I', t.name), p.privilege),
  format('%s has no %s privilege on %s', r.role, p.privilege, t.name)
)
from _memory_tables t
cross join (values ('anon'), ('authenticated')) as r(role)
cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                   ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) as p(privilege)
order by t.name, r.role, p.privilege;

select throws_ok(
  format('set local role %I; %s', r.role, replace(op.sql, '$t', t.name)),
  '42501', null, format('%s cannot %s %s', r.role, op.kind, t.name)
)
from _memory_tables t
cross join (values ('anon'), ('authenticated')) as r(role)
cross join (values
  ('SELECT', 'select * from public.$t'),
  ('INSERT', 'insert into public.$t default values'),
  ('UPDATE', 'update public.$t set created_at = now()'),
  ('DELETE', 'delete from public.$t')
) as op(kind, sql)
order by t.name, r.role, op.kind;

insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000001a'),
  ('00000000-0000-0000-0000-00000000001b');
insert into public.profiles (id, display_name) values
  ('00000000-0000-0000-0000-00000000001a', 'Alice'),
  ('00000000-0000-0000-0000-00000000001b', 'Bob');
insert into public.agents (id, owner_id, name) values
  ('10000000-0000-0000-0000-00000000001a', '00000000-0000-0000-0000-00000000001a', 'AI-A'),
  ('10000000-0000-0000-0000-00000000001b', '00000000-0000-0000-0000-00000000001b', 'AI-B');
insert into public.conversations (id) values
  ('30000000-0000-0000-0000-000000000001'),
  ('30000000-0000-0000-0000-000000000002');
insert into public.memory_tombstones (agent_id, dedupe_key) values
  ('10000000-0000-0000-0000-00000000001a', 'hash-a'),
  ('10000000-0000-0000-0000-00000000001b', 'hash-b');
insert into public.memory_source_suppressions (agent_id, source_conversation_id) values
  ('10000000-0000-0000-0000-00000000001a', '30000000-0000-0000-0000-000000000001'),
  ('10000000-0000-0000-0000-00000000001b', '30000000-0000-0000-0000-000000000001'),
  ('10000000-0000-0000-0000-00000000001a', '30000000-0000-0000-0000-000000000002');
insert into public.memory_completions (conversation_id) values
  ('30000000-0000-0000-0000-000000000001'),
  ('30000000-0000-0000-0000-000000000002');
select is((select count(*)::int from public.memory_tombstones), 2, 'two tombstones seeded');
select is((select count(*)::int from public.memory_source_suppressions), 3, 'three suppressions seeded');
select is((select count(*)::int from public.memory_completions), 2, 'two completions seeded');

delete from public.conversations where id = '30000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.memory_tombstones), 2,
  'conversation deletion preserves content hashes');
select is((select source_conversation_id::text from public.memory_source_suppressions),
  '30000000-0000-0000-0000-000000000002', 'only unrelated source suppression survives');
select is((select conversation_id::text from public.memory_completions),
  '30000000-0000-0000-0000-000000000002', 'only unrelated completion survives');

delete from public.agents where id = '10000000-0000-0000-0000-00000000001a';
select is((select dedupe_key from public.memory_tombstones), 'hash-b',
  'agent deletion cascades to tombstones and preserves other agents hashes');
select is((select count(*)::int from public.memory_source_suppressions), 0,
  'agent deletion cascades to source suppressions');
select is((select conversation_id::text from public.memory_completions),
  '30000000-0000-0000-0000-000000000002', 'agent deletion preserves unrelated completion');

select * from finish();
rollback;
