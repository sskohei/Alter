-- #22: delete memory text immediately, retain only non-text suppression keys.
create table public.memory_tombstones (
  agent_id uuid not null references public.agents(id) on delete cascade,
  dedupe_key text not null,
  created_at timestamptz not null default now(),
  primary key (agent_id,dedupe_key)
);
create table public.memory_source_suppressions (
  agent_id uuid not null references public.agents(id) on delete cascade,
  source_conversation_id uuid not null references public.conversations(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (agent_id,source_conversation_id)
);
create index memory_source_suppressions_source_idx on public.memory_source_suppressions(source_conversation_id);
create table public.memory_completions (
  conversation_id uuid primary key references public.conversations(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.memory_tombstones enable row level security;
alter table public.memory_source_suppressions enable row level security;
alter table public.memory_completions enable row level security;
revoke all on public.memory_tombstones, public.memory_source_suppressions, public.memory_completions
from anon,authenticated;
