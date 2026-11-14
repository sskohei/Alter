-- #22: every conversation purge also refreshes its derived relationship cache.
-- BEFORE DELETE can still resolve both participants, before their FK CASCADE runs.
create function public.recompute_relationship_on_conversation_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  pair record;
  remaining_count integer;
  remaining_last timestamptz;
begin
  for pair in
    select p.agent_id as low_id, q.agent_id as high_id
    from public.conversation_participants p
    join public.conversation_participants q on q.conversation_id=p.conversation_id
      and p.agent_id<q.agent_id
    where p.conversation_id=old.id
  loop
    perform 1 from public.agent_relationships
    where agent_low_id=pair.low_id and agent_high_id=pair.high_id for update;
    select count(*)::integer, max(c.ended_at) into remaining_count, remaining_last
    from public.conversations c
    join public.conversation_participants p on p.conversation_id=c.id and p.agent_id=pair.low_id
    join public.conversation_participants q on q.conversation_id=c.id and q.agent_id=pair.high_id
    where c.id<>old.id and c.status='completed'
      and c.created_at>now()-interval '90 days'
      and not exists (
        select 1 from public.conversation_participants hp
        join public.agents ha on ha.id=hp.agent_id
        join public.profiles hu on hu.id=ha.owner_id
        where hp.conversation_id=c.id and (hu.deleted_at is not null or ha.status='deleted')
      );
    if remaining_count=0 then
      delete from public.agent_relationships
      where agent_low_id=pair.low_id and agent_high_id=pair.high_id;
    else
      update public.agent_relationships
      set conversation_count=remaining_count, last_met_at=remaining_last
      where agent_low_id=pair.low_id and agent_high_id=pair.high_id;
    end if;
  end loop;
  return old;
end;
$$;
create trigger conversations_recompute_relationship_before_delete
before delete on public.conversations
for each row execute function public.recompute_relationship_on_conversation_delete();
revoke all on function public.recompute_relationship_on_conversation_delete()
from public, anon, authenticated;
