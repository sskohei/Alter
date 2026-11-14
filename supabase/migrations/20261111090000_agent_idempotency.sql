-- #15: successful create response only; never store plaintext private information.
create table public.api_idempotency (
  owner_id uuid not null references public.profiles(id) on delete cascade,
  operation varchar(64) not null,
  key_hash text not null,
  request_hash text not null,
  hmac_key_version integer not null check (hmac_key_version > 0),
  response jsonb not null,
  created_at timestamptz not null default now(),
  primary key (owner_id, operation, key_hash)
);
create index api_idempotency_created_at_idx on public.api_idempotency (created_at);
alter table public.api_idempotency enable row level security;
revoke all on public.api_idempotency from anon, authenticated;
