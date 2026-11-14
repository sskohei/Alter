-- Durable, body-free LLM execution state. A crashed in-flight call is never retried.
-- Usage reserve/charge/release is the financial source of truth (12 months).
-- Rollback: drop table public.llm_runs (only after reconciling all open calls).
create table public.llm_runs (
  id uuid primary key,
  agent_id uuid not null references public.agents(id) on delete cascade,
  conversation_id uuid references public.conversations(id) on delete set null,
  purpose text not null check (purpose in ('suggestion', 'conversation')),
  status text not null check (status in (
    'reserved', 'generating', 'generated', 'moderating',
    'succeeded', 'rejected', 'failed', 'unknown'
  )),
  reserved_usd numeric(12,6) not null check (reserved_usd >= 0),
  error_code text check (error_code in ('unavailable', 'unsafe', 'unknown_outcome')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (purpose <> 'conversation' or conversation_id is not null or status in (
    'succeeded', 'rejected', 'failed'
  ))
);
create index llm_runs_agent_created_idx on public.llm_runs(agent_id, created_at);
create index llm_runs_conversation_idx on public.llm_runs(conversation_id);
-- Even if the process dies, a subsequent request cannot spend behind the unknown call.
create unique index llm_runs_one_unsettled_agent_idx on public.llm_runs(agent_id)
where status in ('reserved', 'generating', 'generated', 'moderating', 'unknown');
create trigger llm_runs_updated_at before update on public.llm_runs
for each row execute function public.set_updated_at();
alter table public.llm_runs enable row level security;
revoke all on public.llm_runs from public, anon, authenticated;
