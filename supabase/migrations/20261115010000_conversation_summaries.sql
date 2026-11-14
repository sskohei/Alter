-- #95: Separate, budgeted summary execution; no raw model responses are persisted.
-- Rollback: reconcile all summary runs, remove them, then restore the old purpose check.
alter table public.llm_runs drop constraint llm_runs_purpose_check;
alter table public.llm_runs add constraint llm_runs_purpose_check
  check (purpose in ('suggestion', 'conversation', 'summary'));
alter table public.llm_runs add constraint llm_runs_summary_conversation_check
  check (purpose <> 'summary' or conversation_id is not null
         or status in ('succeeded', 'rejected', 'failed'));
create unique index llm_runs_one_summary_per_conversation_idx
  on public.llm_runs(conversation_id) where purpose='summary';
