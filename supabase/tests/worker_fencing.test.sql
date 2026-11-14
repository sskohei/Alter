-- #20: Worker execution metadata remains server-only.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(22);

select ok(c.relrowsecurity, format('%s has RLS enabled', c.relname))
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('llm_invocations', 'conversation_events')
order by c.relname;
select is((select count(*)::int from pg_policies where schemaname = 'public'
  and tablename in ('llm_invocations', 'conversation_events')), 0, 'No browser RLS policies');

select throws_ok(format('set local role %I; %s', r.role, replace(op.sql, '$t', t.name)),
  '42501', null, format('%s cannot %s %s', r.role, op.kind, t.name))
from (values ('anon'), ('authenticated')) as r(role)
cross join (values ('llm_invocations'), ('conversation_events')) as t(name)
cross join (values
  ('SELECT', 'select * from public.$t'),
  ('INSERT', 'insert into public.$t default values'),
  ('UPDATE', 'update public.$t set created_at = now()'),
  ('DELETE', 'delete from public.$t')
) as op(kind, sql)
order by r.role, t.name, op.kind;

select col_type_is('public', 'background_jobs', 'lease_token', 'uuid', 'UUID lease fencing');
select has_pk('public', 'llm_invocations', 'One marker per conversation and turn');
select has_pk('public', 'conversation_events', 'One event per conversation and kind');
select * from finish();
rollback;
