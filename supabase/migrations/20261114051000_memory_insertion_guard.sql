-- #25 read/delete extraction from #22: do not restore suppressed source/hash content.
-- Serialize candidate writes and deletion on the owning agent, including legacy Workers.
create function public.guard_memory_candidate()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    perform id from public.agents where id=new.agent_id for update;
  elsif new.agent_id is distinct from old.agent_id
        or new.dedupe_key is distinct from old.dedupe_key
        or new.source_conversation_id is distinct from old.source_conversation_id
        or new.text is distinct from old.text
        or new.created_at is distinct from old.created_at then
    -- Candidates are immutable; only status/importance may change. No row→agent lock inversion.
    raise exception 'memory candidate identity and text are immutable' using errcode='23514';
  end if;
  if exists(select 1 from public.memory_tombstones
            where agent_id=new.agent_id and dedupe_key=new.dedupe_key)
     or exists(select 1 from public.memory_source_suppressions
               where agent_id=new.agent_id and source_conversation_id=new.source_conversation_id) then
    return null;
  end if;
  return new;
end;
$$;
create trigger memories_guard_candidate
before insert or update on public.memories
for each row execute function public.guard_memory_candidate();
