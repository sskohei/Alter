# リポジトリ・デプロイ・環境

## Monorepo配置

```text
apps/web/                 Next.js App Router
apps/api/                 FastAPI HTTP入口
apps/worker/              DBジョブ処理
apps/discord_bot/         discord.py Gateway/Slash Commands
apps/scheduler/           Render Cronの短命入口
backend/alter_domain/     Python共通ドメインパッケージ
packages/api-client/      OpenAPI生成TypeScriptクライアント
supabase/migrations/      SQL/RLS唯一のDDL正本
docs/                     本仕様と意思決定
.github/workflows/        lint/test/OpenAPI差分
render.yaml               Render Blueprint（デプロイIssueで追加予定）
pnpm-workspace.yaml
pyproject.toml            uv workspace
docker-compose.yml        ローカル補助（任意、現在は未使用）
.env.example              ダミー値・取得先・必須/任意
```

`uv`でPythonの共通パッケージをworkspace依存にし、Render各サービスはrepo rootをbuild contextとして同じlockfileを使う。`pnpm`もlockfileをrootに置く。ローカルはSupabase CLIでDB/Authを起動し、`uv run`でAPI/Worker/Bot（Botはテスト用ギルド）、`pnpm --filter web dev`でWebを起動する。`docker-compose.yml`は任意の`api`/`worker`/`bot`プロファイルと環境変数読み込みを定義し、DB/AuthはSupabase CLIのローカル環境を参照する。Compose内に別のDBを作らない。CIではRuff、mypy、pytest、ESLint、TypeScript、Vitest、Playwrightの必要ゲートを実行する。

Issue #2で開発用雛形を配置済み。固定バージョン、初回設定、起動・停止・reset、Mockの制約は[README](../README.md#ローカル開発最初に実行)を参照。現時点の各入口は起動確認用で、業務処理・認証・本番デプロイは後続Issueで実装する。

## 配置と起動

| サービス | 配置 | 起動と重要設定 |
|---|---|---|
| Next.js | Vercel | `apps/web`、Preview/Productionを別環境。FastAPI URLとSupabase URL/Publishable Keyを公開設定 |
| FastAPI | Render有料Web Service | `uvicorn ... --host 0.0.0.0 --port $PORT`、health/readiness、CORS許可Origin固定。`ALTER_ENVIRONMENT=production`では`/docs`・`/redoc`を出さない |
| Worker | Render有料Background Worker | ジョブループ、並列数1から開始、lease更新と終了シグナル対応 |
| Bot | Render有料Background Worker | Gateway常時接続、再接続、コマンド同期はデプロイ時のみ |
| Scheduler | Render Cron Job | `*/15 * * * *` UTC、1回実行で終了。DB一意制約で多重作成抑止 |
| DB/Auth | Supabase | 本番とPreviewは別プロジェクト。RLS、バックアップ、Auth redirect allowlist |

BotとWorkerは無料Web Serviceでは常時動作しない。[Render無料制約](https://render.com/docs/free)では無料Webが15分無通信で停止し、Background Workerに無料プランがない。[Cron公式](https://render.com/docs/cronjobs)は月額最低$1で、UTC指定。確認日2026-10-27。API・Bot・Workerを単一サービスに詰める案は障害分離とデプロイ単位を損なうため採用しない。RedisはMVP不要。

## 環境変数と秘密

| 対象 | 変数名 | 用途 / 公開可否 |
|---|---|---|
| Web | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL` | 公開可。秘密を`NEXT_PUBLIC_`へ置かない |
| Web server | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | SSRクライアント。公開可能な値だがサーバー設定にも保持 |
| API | `ALTER_ENVIRONMENT`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`, `WEB_URL`, `CORS_ORIGINS`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `PROFILE_ENCRYPTION_KEY_V1` | DB/OAuth/暗号鍵はRender Secret。`CORS_ORIGINS`はCSVで明示 |
| Worker | `DATABASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `MAX_DAILY_COST_USD`, `JOB_LEASE_SECONDS` | LLMキーをWebに渡さない |
| Bot | `DATABASE_URL`, `DISCORD_BOT_TOKEN`, `WEB_URL`, `DISCORD_CLIENT_ID` | Bot tokenはRender Secret |
| Cron | `DATABASE_URL`, `SCHEDULER_BATCH_SIZE` | バッチ上限を設ける |
| DB migration | `SUPABASE_DB_URL` | CI/管理者のみ。通常サービスに配らない |

### APIの環境別設定（#34、#35 と対応）

Preview/Productionでそれぞれ設定し、他の環境の値を流用しない。JWTの発行元を環境ごとに分けることで、PreviewのトークンをProductionのAPIが受け付けない。

| 変数 | local | Preview | Production |
|---|---|---|---|
| `ALTER_ENVIRONMENT` | `local`（既定） | `preview` | `production` |
| `SUPABASE_URL` | `http://127.0.0.1:54321` | Preview用Supabaseプロジェクト | 本番用Supabaseプロジェクト |
| `SUPABASE_JWT_ISSUER` | `http://127.0.0.1:54321/auth/v1` | `https://<preview-project>.supabase.co/auth/v1` | `https://<prod-project>.supabase.co/auth/v1` |
| `SUPABASE_JWT_AUDIENCE` | `authenticated` | `authenticated` | `authenticated` |
| `SUPABASE_SECRET_KEY` | `supabase status`のSecret | その環境のSecret Key（Render Secret） | その環境のSecret Key（Render Secret） |
| `CORS_ORIGINS` | `http://localhost:3000` | VercelのPreview用`NEXT_PUBLIC_SITE_URL`と完全一致 | VercelのProduction用`NEXT_PUBLIC_SITE_URL`と完全一致 |

`SUPABASE_SECRET_KEY`はAPI/運営CLIだけに置き、Web・Worker・Bot・Cronへ渡さない。`CORS_ORIGINS`に`*`は指定できない（起動時に拒否）。

### APIドキュメントの公開範囲

`/openapi.json`はすべての環境で公開する（Vercelのスモーク確認とTypeScript生成に使い、APIの形だけで秘密は含まない）。Swagger UIの`/docs`とReDocの`/redoc`は`ALTER_ENVIRONMENT=production`のときだけ無効にし（404）、local/Previewでは従来どおり表示する。`ALTER_ENVIRONMENT`は`local`/`preview`/`production`以外の値を起動時に拒否し、綴りの誤りで本番の画面が開いたままにならないようにする。

`.env.example`には上の変数名・ダミー値・取得先・必須/任意を記載し、実値はGitに入れない。RenderのEnvironment Groupは共通非秘密値だけ、秘密はサービス単位。Discord OAuth secretはコールバックを処理するAPIだけに置く。Supabase server secretは通常CRUDに使わず、Authユーザー削除・招待時のidentity照合の限定管理操作だけに使用し、専用環境変数`SUPABASE_SECRET_KEY`をAPIに限定する。

## 招待管理の運営手順（ADR-014、#7で実装）

招待者の一覧は環境変数・設定ファイルへ置かず、環境ごとのDBで管理する。sskoheiがAPI実行環境内の運営専用CLIから対象環境を確認し、本人を私的経路で照合して登録・一覧確認・identity追加・失効を行う。CLIは共通ドメイン処理とDB接続を使い、HTTPの管理エンドポイントは追加しない。資格情報はサーバー側のSecret管理から読み、Web・コマンド引数・ログへ渡さない。

初回OAuth後のID確認はSupabase Auth管理情報を使う。Auth照会に管理鍵が必要な場合はAPIの限定管理処理でのみ使用し、Worker/Bot/Cronへ配布しない。CLIの識別子入力方法・コマンド・失敗時の再実行手順は #7 で実装と同時に本節へ追記する。Preview/Productionの招待データをコピーせず、それぞれ本人照合して登録する。復元時は招待失効・削除を再適用してからAPI/Worker/Bot/Cronを再開する。

## デプロイ手順

1. Supabaseの本番/Previewプロジェクトを作り、Google/GitHub OAuth、Redirect URL、JWT signing、バックアップを設定する。
2. CIでSQL migrationを検証し、本番へのDDLはレビュー済みSQLを一方向適用。下位互換migrationを先行する。
3. RenderのAPI→Worker→Bot→CronをBlueprintで配置し、Secretsを個別設定。SupabaseへのTLS接続とIPv4/プーラー方式を実環境で検証する。[Supabase接続](https://supabase.com/docs/guides/database/connecting-to-postgres)。
4. Vercel PreviewでE2E、ProductionでAuth callbackとCORSを確認し、Discordテストギルドへ1件投稿する。
5. rollbackはアプリを前版へ戻す。破壊的DDLは別リリースで実行する。DBバックアップ復元手順を定期演習する。

### 自律交流ONを有効にする設定（#14・#16・#24）

送信先LLMが決まり同意文面を確定したら、同じ環境で次の3つを同時に設定する。Webの`ALTER_LLM_NAME`と`ALTER_LLM_DATA_POLICY_URL`（同意画面に表示）、APIの`ALTER_CONSENT_VERSION`（監査に記録する文面の版）。どれかが空の間は自律交流をONにできない（WebはONボタンを出さず、APIは409）。片方だけ設定すると「ONボタンは押せるがAPIが409」になるため、Preview/Productionそれぞれでそろえてから公開する。文面を変えたら版も更新する。

費用と無料枠は [費用試算](20-cost-estimation.md)。
