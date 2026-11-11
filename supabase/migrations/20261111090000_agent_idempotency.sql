-- #15: successful create response only; never store plaintext private information.
create table public.api_idempotency (
  owner_id uuid not null references public.profiles(id) on delete cascade,
  operation varchar(64) not null,
  key_hash text not null,
  request_hash text not null,
  response jsonb not null,
  created_at timestamptz not null default now(),
  primary key (owner_id, operation, key_hash)
);
alter table public.api_idempotency enable row level security;
revoke all on public.api_idempotency from anon, authenticated;
