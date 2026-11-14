-- Issue #32: 全テーブル・関数・Storage への直接アクセスの横断確認（pgTAP）
-- 実行: pnpm exec supabase test db
--
-- 個別の表のテストとは別に、public の全オブジェクトを毎回列挙して確かめる。
-- 後から追加した表・関数・シーケンスも、権限を外し忘れれば失敗する。
begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(8);

-- 1. 表・ビュー: anon / authenticated はどの権限も持たない
select is(
  (select coalesce(array_agg(format('%s:%s:%s', r.role, c.relname, p.priv)
                             order by r.role, c.relname, p.priv), '{}')
   from pg_class c
   join pg_namespace n on n.oid = c.relnamespace
   cross join (values ('anon'), ('authenticated')) as r (role)
   cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'),
                      ('REFERENCES'), ('TRIGGER')) as p (priv)
   where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'f')
     and has_table_privilege(r.role, c.oid, p.priv)),
  '{}'::text[],
  'anon / authenticated は public の表・ビューに権限を持たない'
);

-- 2. 列単位の権限も持たない（表の権限を外しても列 GRANT が残る場合を拾う）
select is(
  (select count(*)::int
   from information_schema.column_privileges
   where table_schema = 'public' and grantee in ('anon', 'authenticated')),
  0,
  'anon / authenticated は public の列に権限を持たない'
);

-- 3. シーケンス: 使用・参照・更新できない
select is(
  (select coalesce(array_agg(format('%s:%s', r.role, c.relname) order by r.role, c.relname),
                   '{}')
   from pg_class c
   join pg_namespace n on n.oid = c.relnamespace
   cross join (values ('anon'), ('authenticated')) as r (role)
   where n.nspname = 'public' and c.relkind = 'S'
     and (has_sequence_privilege(r.role, c.oid, 'USAGE')
          or has_sequence_privilege(r.role, c.oid, 'SELECT')
          or has_sequence_privilege(r.role, c.oid, 'UPDATE'))),
  '{}'::text[],
  'anon / authenticated は public のシーケンスを使えない'
);

-- 4. 関数: public の関数を実行できない（PUBLIC への既定の EXECUTE も外れている）
select is(
  (select coalesce(array_agg(format('%s:%s', r.role, p.oid::regprocedure)
                             order by r.role, p.oid::regprocedure::text), '{}')
   from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   cross join (values ('anon'), ('authenticated')) as r (role)
   where n.nspname = 'public'
     and not exists (select 1 from pg_depend d
                     where d.objid = p.oid and d.deptype = 'e')  -- 拡張の関数は対象外
     and has_function_privilege(r.role, p.oid, 'EXECUTE')),
  '{}'::text[],
  'anon / authenticated は public の関数を実行できない'
);

-- 5. public にオブジェクトを作れない（本物の Supabase の初期権限でも false を確認済み、2026-11-14）
select ok(
  not has_schema_privilege('anon', 'public', 'CREATE')
  and not has_schema_privilege('authenticated', 'public', 'CREATE'),
  'anon / authenticated は public にオブジェクトを作れない'
);

-- 6. RLS: 全テーブルで有効（ポリシー 0 件の default deny）
select is(
  (select coalesce(array_agg(c.relname::text order by c.relname), '{}')
   from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity),
  '{}'::text[],
  'public の全テーブルで RLS が有効'
);

-- 7. 所有者以外へ RLS を回避させる SECURITY DEFINER の関数が public にない
select is(
  (select coalesce(array_agg(p.oid::regprocedure::text order by p.oid::regprocedure::text),
                   '{}')
   from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public' and p.prosecdef
     and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
     and (has_function_privilege('anon', p.oid, 'EXECUTE')
          or has_function_privilege('authenticated', p.oid, 'EXECUTE'))),
  '{}'::text[],
  'anon / authenticated から呼べる SECURITY DEFINER の関数がない'
);

-- 8. Storage: 公開 bucket がない（画像は署名 URL だけで渡す。docs/15）
select is(
  (select coalesce(array_agg(id order by id), '{}') from storage.buckets where public),
  '{}'::text[],
  'Storage に公開 bucket がない'
);

select * from finish();
rollback;
