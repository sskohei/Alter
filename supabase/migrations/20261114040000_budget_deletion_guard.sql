-- Keep reservation references until reconciliation/release. Deletion must not silently
-- strand holds via ON DELETE SET NULL or anonymization. Do not auto-release unknown
-- provider outcomes. #30 calls budget.release_for_deletion before deleting, in one tx.
-- Rollback: drop both triggers, then drop function public.guard_budget_deletion().
create function public.guard_budget_deletion()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  -- Same lock as alter_domain.budget. Callers acquire it before agent/setting locks.
  perform pg_catalog.pg_advisory_xact_lock(660018);
  if exists (
    select 1 from public.usage_records
    where (tg_table_name = 'agents' and agent_id = old.id)
       or (tg_table_name = 'conversations' and conversation_id = old.id)
    group by agent_id, conversation_id, provider, model
    having sum(case when kind = 'reserve' then estimated_usd
                    when kind = 'release' then -estimated_usd else 0 end) > 0
  ) then
    raise exception 'reconcile and release outstanding budget holds before deletion'
      using errcode = 'check_violation';
  end if;
  return old;
end;
$$;

revoke all on function public.guard_budget_deletion() from public, anon, authenticated;
create trigger agents_guard_budget_deletion
before delete on public.agents
for each row execute function public.guard_budget_deletion();
create trigger conversations_guard_budget_deletion
before delete on public.conversations
for each row execute function public.guard_budget_deletion();
