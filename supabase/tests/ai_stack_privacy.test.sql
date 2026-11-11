-- Cross-stack security baseline for #15/#20/#22 before #38 measurement/export.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path=public,extensions;
select plan(55);
create temporary table _ai_tables(name text primary key) on commit drop;
insert into _ai_tables values ('api_idempotency'),('llm_invocations'),('conversation_events'),
  ('memory_tombstones'),('memory_source_suppressions'),('memory_completions');
select ok(c.relrowsecurity,format('%s enables RLS',c.relname))
from pg_class c join pg_namespace n on n.oid=c.relnamespace join _ai_tables t on t.name=c.relname
where n.nspname='public' order by c.relname;
select is((select count(*)::int from pg_policies p join _ai_tables t on t.name=p.tablename
  where p.schemaname='public'),0,'No direct browser policies');
select throws_ok(format('set local role %I; %s',r.role,replace(op.sql,'$t',t.name)),
  '42501',null,format('%s cannot %s on %s',r.role,op.kind,t.name))
from (values ('anon'),('authenticated')) r(role) cross join _ai_tables t
cross join (values ('SELECT','select * from public.$t'),('INSERT','insert into public.$t default values'),
  ('UPDATE','update public.$t set created_at=now()'),('DELETE','delete from public.$t')) op(kind,sql)
order by r.role,t.name,op.kind;
select * from finish();
rollback;
