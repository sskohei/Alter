-- #15: server-only replay records and owner deletion.
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(14);

select ok(
  (select relrowsecurity from pg_class where oid = 'public.api_idempotency'::regclass),
  'api_idempotency: RLS enabled'
);
select is(
  (select count(*)::int from pg_policies
   where schemaname = 'public' and tablename = 'api_idempotency'),
  0, 'api_idempotency: no policies'
);
select throws_ok(
  format('set local role %I; %s', r.role, op.sql),
  '42501', null, format('%s cannot %s api_idempotency', r.role, op.kind)
)
from (values ('anon'), ('authenticated')) as r(role)
cross join (values
  ('SELECT', 'select * from public.api_idempotency'),
  ('INSERT', 'insert into public.api_idempotency default values'),
  ('UPDATE', 'update public.api_idempotency set created_at = now()'),
  ('DELETE', 'delete from public.api_idempotency')
) as op(kind, sql);

insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000001a'),
  ('00000000-0000-0000-0000-00000000001b');
insert into public.profiles (id, display_name) values
  ('00000000-0000-0000-0000-00000000001a', 'Alice'),
  ('00000000-0000-0000-0000-00000000001b', 'Bob');
insert into public.api_idempotency
  (owner_id, operation, key_hash, request_hash, hmac_key_version, response) values
  ('00000000-0000-0000-0000-00000000001a', 'agent.create', 'key', 'digest', 1, '{}'),
  ('00000000-0000-0000-0000-00000000001b', 'agent.create', 'key', 'digest', 2, '{}');
select throws_ok(
  $$insert into public.api_idempotency
    (owner_id, operation, key_hash, request_hash, hmac_key_version, response) values
    ('00000000-0000-0000-0000-00000000001a', 'agent.create', 'key', 'digest', 1, '{}')$$,
  '23505', null, 'same owner/operation/key cannot be duplicated'
);
select throws_ok(
  $$update public.api_idempotency set hmac_key_version = 0$$,
  '23514', null, 'HMAC key version must be positive'
);
delete from public.profiles where id = '00000000-0000-0000-0000-00000000001a';
select is(
  (select count(*)::int from public.api_idempotency
   where owner_id = '00000000-0000-0000-0000-00000000001a'),
  0, 'profile deletion cascades to replay records'
);
select is(
  (select count(*)::int from public.api_idempotency
   where owner_id = '00000000-0000-0000-0000-00000000001b'),
  1, 'other owners replay records are retained'
);
select * from finish();
rollback;
