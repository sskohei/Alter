# 技術スタックと選定

## 確定と追加採用

| 層 | 採用 | 理由 |
|---|---|---|
| Web | Next.js App Router / TypeScript / Tailwind CSS / shadcn/ui | SSR認証と画面開発。デザインもコードで作る（ADR-015） |
| 認証・DB | Supabase Auth / PostgreSQL | ID統一、RLS、永続化 |
| API | FastAPI / Pydantic | OpenAPIを契約とし、業務ロジックを集約 |
| DBアクセス | SQLAlchemy 2.x + psycopg + SQL文併用 | トランザクションと明示的ロックが必要。Bot/Workerで共有 |
| マイグレーション | Supabase CLIのSQL migrationのみ | RLS/トリガー/索引まで一つの正本にする。Alembicは採用しない |
| ジョブ | PostgreSQL `background_jobs` + `FOR UPDATE SKIP LOCKED` | 10〜100人規模でRedis/Celeryを省く |
| スケジュール | Render Cron Job | APIプロセスから独立し、定期的にジョブを追加 |
| Discord | discord.py 2.x / Discord OAuth2 | Slash CommandとGateway接続。投稿はBot APIを基本とする |
| フロントデータ | TanStack Query / React Hook Form / Zod / OpenAPI TypeScript生成 | Polling、フォーム状態、型の二重管理を減らす |
| Monorepo | pnpm workspace / uv workspace | それぞれの依存を固定し、Python共通パッケージを共有 |

FastAPIからはSupabase Postgresに直接接続する。`supabase-py`はAuth管理用途に限って検討し、主要テーブルCRUDには採用しない。接続方式、RLSとの責務分離は [DB](05-database-design.md) と [認証](06-authentication-authorization.md)。pgvectorは検索品質と件数が閾値を超えた時だけ導入する。APSchedulerはAPI複製時に多重起動リスクがあるため採用しない。Redis/Celeryはジョブ処理量や待ち時間がPostgres方式の限界を示してから移行する。

## バージョンと契約

着手時にNext.js、Python、discord.pyなどのサポート版をロックファイルに固定し、CIで更新を検証する。特定のメジャーバージョンに依存するSupabase SSRのProxy/Middleware命名は導入版に合わせる。[Supabase SSR公式資料](https://supabase.com/docs/guides/auth/server-side/creating-a-client)ではNext.js 16以降に`proxy.ts`、15以前に`middleware.ts`を用いる。OpenAPIはFastAPIから生成し、`packages/api-client`にTypeScript型を生成する。API変更PRは生成差分を含める。

Issue #2の雛形ではNode.js 24.18.0 / Python 3.13.12 / pnpm 10.34.9 / uv 0.13.6を固定した。Next.js 16.4.3 / React 19.3.1 / Supabase CLI 2.122.0を採用し、全依存の解決結果は`pnpm-lock.yaml` / `uv.lock`で管理する。画面部品、認証・フォーム等のライブラリは利用機能の実装時に追加する。

## 比較の結論

| 論点 | 採用 | 今は採用しない理由 / 再検討条件 |
|---|---|---|
| Postgres直結 vs supabase-py | 直結 | 複数テーブルの原子操作とロックが重要。REST経由が必要なら再検討 |
| SQL migrations vs Alembic | Supabase CLI | RLSを含むDDLの正本を一本化 |
| DBジョブ vs Redis/Celery | DBジョブ | 運用対象を減らす。待機時間>1分または数千件/日で再評価 |
| Polling vs SSE/Realtime | 15〜30秒Polling | 同時閲覧と即時性要求が増えたらSSE。Supabase Realtime直読は権限設計後 |
| テキスト検索 vs pgvector | PostgreSQL索引と限定候補 | 記憶が各AI数百件超、再現率不足が測定されたらpgvector |
| Bot API vs Webhook表示 | Bot API | スレッドと権限を一貫管理。個別アイコン演出が必要ならWebhook権限を追加 |

公式資料確認日: 2026-10-27。外部仕様は [デプロイ](14-deployment-infrastructure.md) と [費用](20-cost-estimation.md) に出典を記す。
