-- #15: normalized icons are separate from private profile assets. Both buckets are private.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('agent-icons', 'agent-icons', false, 2097152, array['image/png']),
       ('profile-private', 'profile-private', false, 2097152, null);

create table public.agent_icon_uploads (
  path text primary key,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  content_type text not null check (content_type = 'image/png'),
  size_bytes integer not null check (size_bytes between 1 and 2097152),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  constraint agent_icon_owner_path check (
    path ~ ('^' || owner_id::text || '/[0-9a-f-]{36}\.png$')
  )
);
create index agent_icon_uploads_owner_idx on public.agent_icon_uploads(owner_id);
alter table public.agent_icon_uploads enable row level security;
revoke all on table public.agent_icon_uploads from public, anon, authenticated;
-- Restrictive policy also protects these buckets if a future unrelated permissive policy exists.
create policy agent_assets_server_only on storage.objects as restrictive
  for all to anon, authenticated
  using (bucket_id not in ('agent-icons', 'profile-private'))
  with check (bucket_id not in ('agent-icons', 'profile-private'));
-- Files are written with the API-only storage credential using UUID paths and upsert=false.
alter table public.agents add constraint agents_icon_upload_fk
  foreign key (icon_path) references public.agent_icon_uploads(path) on delete set null;
