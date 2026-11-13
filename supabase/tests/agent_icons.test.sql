begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(18);

select is((select public from storage.buckets where id='agent-icons'), false,
          'icon bucket is private');
select is((select public from storage.buckets where id='profile-private'), false,
          'private profile assets have a separate private bucket');
select is((select file_size_limit from storage.buckets where id='agent-icons'),
          2097152::bigint, 'icon bucket has 2 MiB limit');
select is((select allowed_mime_types from storage.buckets where id='agent-icons'),
          array['image/png']::text[], 'only normalized PNG objects');
select ok((select relrowsecurity from pg_class where oid='public.agent_icon_uploads'::regclass),
          'upload registry uses RLS');
select is((select count(*)::int from pg_policies where schemaname='public'
           and tablename='agent_icon_uploads'), 0, 'registry has no browser policies');
select is((select permissive from pg_policies where schemaname='storage'
           and tablename='objects' and policyname='agent_assets_server_only'),
          'RESTRICTIVE', 'asset policy cannot be overridden by a permissive policy');
select throws_ok(format('set local role %I; %s', r.role, op.sql), '42501', null,
                 format('%s cannot %s upload registry', r.role, op.kind))
from (values ('anon'),('authenticated')) r(role)
cross join (values ('SELECT','select * from public.agent_icon_uploads'),
 ('INSERT','insert into public.agent_icon_uploads default values'),
 ('UPDATE','update public.agent_icon_uploads set content_type=''image/png'''),
 ('DELETE','delete from public.agent_icon_uploads')) op(kind,sql);

insert into auth.users(id) values ('00000000-0000-4000-8000-00000000001a');
insert into public.profiles(id,display_name)
 values ('00000000-0000-4000-8000-00000000001a','icon owner');
insert into public.agent_icon_uploads(path,owner_id,content_type,size_bytes,sha256)
 values ('00000000-0000-4000-8000-00000000001a/00000000-0000-4000-8000-00000000001b.png',
         '00000000-0000-4000-8000-00000000001a','image/png',100,repeat('a',64));
select throws_ok($$insert into public.agent_icon_uploads values (
  '00000000-0000-4000-8000-00000000001a/00000000-0000-4000-8000-00000000001c.png',
  '00000000-0000-4000-8000-00000000001a','image/svg+xml',100,repeat('b',64),now())$$,
  '23514',null,'registry cannot claim SVG as a validated icon');
delete from public.profiles where id='00000000-0000-4000-8000-00000000001a';
select is((select count(*)::int from public.agent_icon_uploads where
 owner_id='00000000-0000-4000-8000-00000000001a'),0,'owner deletion cascades registry');

-- An unrelated permissive policy must still not authorize overwriting icons.
create policy test_broad_objects on storage.objects for all to authenticated
 using (true) with check (true);
select throws_ok($$set local role authenticated;
 insert into storage.objects(bucket_id,name) values ('agent-icons','unvalidated.png')$$,
 '42501',null,'restrictive policy prevents browser writes even with broad permission');

select * from finish();
rollback;
