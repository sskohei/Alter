begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;
select plan(17);
select has_table('public','llm_runs','Durable LLM metadata exists');
select ok((select relrowsecurity from pg_class where oid='public.llm_runs'::regclass),'RLS enabled');
select is((select count(*)::int from pg_policies where schemaname='public' and tablename='llm_runs'),0,'No browser policies');
select ok(not has_table_privilege('anon','public.llm_runs','SELECT'),'anon cannot read');
select ok(not has_table_privilege('authenticated','public.llm_runs','INSERT'),'browser cannot insert');
select ok(not has_table_privilege('authenticated','public.llm_runs','SELECT'),'browser cannot read');
select ok(not has_table_privilege('authenticated','public.llm_runs','UPDATE'),'browser cannot update');
select ok(not has_table_privilege('authenticated','public.llm_runs','DELETE'),'browser cannot delete');
select has_index('public','llm_runs','llm_runs_one_unsettled_agent_idx','One open call per AI');
select is((select count(*)::int from information_schema.columns where table_schema='public'
  and table_name='llm_runs' and column_name in ('prompt','output','answers','private_info','token')),0,'No body or credential storage');
insert into auth.users(id) values ('71000000-0000-0000-0000-000000000001');
insert into public.profiles(id,display_name) values ('71000000-0000-0000-0000-000000000001','Synthetic');
insert into public.agents(id,owner_id,name) values ('71000000-0000-0000-0000-000000000002','71000000-0000-0000-0000-000000000001','AI');
select lives_ok($$insert into public.llm_runs(id,agent_id,purpose,status,reserved_usd)
  values ('71000000-0000-0000-0000-000000000003','71000000-0000-0000-0000-000000000002','suggestion','reserved',0.012448)$$,'Reserve a call');
select throws_ok($$insert into public.llm_runs(id,agent_id,purpose,status,reserved_usd)
  values ('71000000-0000-0000-0000-000000000004','71000000-0000-0000-0000-000000000002','suggestion','generating',0.012448)$$,'23505',null,'Concurrent execution rejected');
select lives_ok($$update public.llm_runs set status='succeeded' where id='71000000-0000-0000-0000-000000000003'$$,'Settle the call');
select lives_ok($$insert into public.llm_runs(id,agent_id,purpose,status,reserved_usd)
  values ('71000000-0000-0000-0000-000000000004','71000000-0000-0000-0000-000000000002','suggestion','unknown',0.012448)$$,'Unknown outcome stays open');
select throws_ok($$update public.llm_runs set status='retry' where id='71000000-0000-0000-0000-000000000004'$$,'23514',null,'Unknown calls cannot become arbitrary retry states');
select has_index('public','llm_runs','llm_runs_agent_created_idx','UTC daily limit lookup');
select has_index('public','llm_runs','llm_runs_conversation_idx','Conversation lookup');
select * from finish();
rollback;
