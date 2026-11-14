-- #20: fencing prevents a stale Worker from saving after lease reclamation.
alter table public.background_jobs add column lease_token uuid;
create table public.llm_invocations (
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  turn_no smallint not null check (turn_no between 1 and 8),
  status varchar(16) not null check (status in ('started','retryable','finished','unknown')),
  created_at timestamptz not null default now(),
  primary key (conversation_id,turn_no)
);
-- Completion event is persisted with status changes; #21/#22 own independent consumer progress.
create table public.conversation_events (
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  kind varchar(32) not null check (kind in ('completed','failed','cancelled')),
  created_at timestamptz not null default now(),
  primary key (conversation_id,kind)
);
alter table public.llm_invocations enable row level security;
alter table public.conversation_events enable row level security;
revoke all on public.llm_invocations, public.conversation_events from anon,authenticated;
