# リポジトリ・デプロイ・環境

## Monorepo配置

```text
apps/web/                 Next.js App Router
apps/api/                 FastAPI HTTP入口
apps/worker/              DBジョブ処理
apps/discord_bot/         discord.py Gateway/Slash Commands
apps/scheduler/           OS定期実行から呼ぶ短命入口
backend/alter_domain/     Python共通ドメインパッケージ
packages/api-client/      OpenAPI生成TypeScriptクライアント
supabase/migrations/      SQL/RLS唯一のDDL正本
docs/                     本仕様と意思決定
.github/workflows/        lint/test/OpenAPI差分
pnpm-workspace.yaml
pyproject.toml            uv workspace
docker-compose.yml        手元PCのプロセス管理候補（未実装、#34で方式選定）
.env.example              ダミー値・取得先・必須/任意
```

`uv`でPythonの共通パッケージをworkspace依存にし、手元PCの各Pythonプロセスはrepo rootと同じlockfileを使う。`pnpm`もlockfileをrootに置く。ローカルはSupabase CLIでDB/Authを起動し、`uv run`でAPI/Worker/Bot（Botはテスト用ギルド）、`pnpm --filter web dev`でWebを起動する。この開発環境のローカルSupabaseと、MVP運用時のクラウドSupabaseを区別する。MVP運用ではDBをPC上に追加せず、Supabase Freeの環境別プロジェクトへ接続する。OSサービス管理またはDocker Composeによる起動/再起動の具体化は #34 で行う。CIではRuff、mypy、pytest、ESLint、TypeScript、Vitest、Playwrightの必要ゲートを実行する。

Issue #2で開発用雛形を配置済み。固定バージョン、初回設定、起動・停止・reset、Mockの制約は[README](../README.md#ローカル開発最初に実行)を参照。認証/人格/活動等のAPIは実装済み。SchedulerのDB交流予約は #19 で実装済み。Workerの会話生成・安全判定・費用精算は #20 で接続済み。実配置の受け入れは #34/#37 で継続する。本節のPC起動設定・HTTPS公開経路・定期実行・バックアップは配置契約であり、本変更では実装/設定していない。

## 配置と起動

| サービス | 配置 | 起動と重要設定 |
|---|---|---|
| Next.js | Vercel Hobby | `apps/web`、Preview/Productionを別環境。公開HTTPSのFastAPI URLと環境別Supabase URL/Publishable Keyを設定 |
| FastAPI | 手元PCの独立プロセス | loopbackで待受け、HTTPS経路を中継。health/readiness、CORS許可Origin固定。`ALTER_ENVIRONMENT=production`では`/docs`・`/redoc`を出さない |
| Worker | 手元PCの独立プロセス | ジョブループ、並列数1から開始、lease更新と終了シグナル対応 |
| Bot | 手元PCの独立プロセス | PC稼働中はGateway接続、再接続、コマンド同期は配置時のみ |
| Scheduler | 手元PCのOS定期実行 | UTC `*/15 * * * *`、1回実行で終了。DB一意制約とOS側の重複起動抑止 |
| Purge | 手元PCのOS定期実行 | `alter-api-purge`（APIパッケージ）、毎時1回実行で終了。アカウント削除のパージと保持期限の削除（ADR-018）。ジョブはskip lockedとリースで重複起動しても二重に処理しない |
| Ops確認 | 手元PCのOS定期実行 | `alter-ops status`（ドメインパッケージ）、15分ごとに実行して終了。件数と警告コードだけを出し、警告があれば終了コード3で運営者へ知らせる。読み取りのみ（[運用手順書](27-operations-runbook.md)） |
| DB/Auth | Supabase Free | 本番/Previewは別プロジェクト。RLS、Auth redirect allowlist。バックアップを別途取得 |

2026-11-14の方針変更（ADR-015、#75）によりRenderへの配置とBlueprint作成は行わない。API・Worker・Botのプロセス境界は保つが、PC・電源・回線は共有する。RedisはMVP不要。

### 無料枠と利用可能時間

Vercel Hobbyは個人・非商用の利用条件と使用量上限を満たす範囲で使う。Supabase Freeは無料プロジェクト数の上限内で本番/Previewを分離し、DB/Storage/転送量と非稼働時の休止条件を確認する。自動バックアップを前提としない。無料枠を超える場合や用途条件を満たさない場合は、公開前にプラン/構成を再検討する。[Vercel Hobby](https://vercel.com/docs/plans/hobby)、[Supabase Free](https://supabase.com/pricing)（確認日2026-11-14）。

運営者はPCの稼働時間を参加者へ案内する。ブラウザを閉じてもPCが稼働していれば生成するが、電源OFF/スリープ/OS再起動/回線断ではAPI・交流・Discord・パージが停止する。WebやAuthが表示できても、APIが必要な閲覧/操作は失敗し得る。WebはAPI障害を表示し、Mock/ローカルAPI/DB直接更新へ自動退避しない。PC停止中の削除依頼窓口と復旧予定を私的経路で案内し、パージ期限を延長しない。

### 公開Webから手元PCへの接続（#34、#35）

- ブラウザとVercelのSSR/Server Routeから到達できる公開HTTPS API URLを確保する。`NEXT_PUBLIC_API_URL=http://localhost:8000`はローカル開発専用であり、公開Webには設定しない。
- 手元PCから外向きに接続するHTTPSトンネル等を基本候補とする。提供元・無料条件・URL固定可否・認証方式は #34 で選定/検証する。公開経路はAPIだけに限定し、DB/SSH/管理画面を公開しない。無料の固定URLを確保できない場合は費用/方式を再検討する。
- APIはloopbackで待受け、中継先をそのポートに固定する。追加の中継認証を使う場合は、ブラウザからのBearer付き要求・CORS preflightとVercelからの通信を妨げないことを確認する。JWT/招待/所有権確認はAPI自身が維持する。
- Vercelの`NEXT_PUBLIC_API_URL`へ公開HTTPS URLを設定して再ビルド/再配置する。`WEB_URL`と`CORS_ORIGINS`は対象WebのOrigin、SupabaseのSite URL/Redirect allowlistとDiscord OAuth redirectはWeb callbackに合わせる。APIのURLが変わっただけならWeb callbackをAPIへ変更しない。
- Preview/ProductionはSupabaseだけでなくAPIのプロセス・待受ポート・公開URL・環境ファイルも分離する。同じPCを共用してもPreviewの要求/秘密をProductionへ流さない。CORSへ任意のPreview URLをワイルドカード登録しない。

### PC上の起動と停止（#34）

repo rootで`uv sync --all-packages --locked`を行い、専用の実行ユーザーで環境ごと・プロセスごとに起動する。運用用checkoutに開発用root `.env`を残さず、OSサービスから対象環境の設定だけを渡す。API運用時は`uv run --package alter-api uvicorn alter_api.main:app --host 127.0.0.1 --port 8000`を基本とし、開発用のreload付き入口を常用しない。Previewは別ポートとする。Worker/BotはREADMEの入口を使い、対応するlive実装の完了後に有効化する。

OSのサービス管理（Linuxならsystemd等）でAPI・Worker・Bot・HTTPS中継を個別に管理し、起動順・作業ディレクトリ・環境ファイル・終了シグナル・再起動回数の制限・本文/秘密を含めないログを設定する。自動起動と自動再起動を検証し、PCのスリープを稼働時間中は無効にする。OSや運用スクリプトの具体化は #34 で行う。

Schedulerは`ALTER_MODE=live`かつ`SCHEDULER_ENABLED=true`でDB交流予約を行う（既定無効、LLMキー不要）。OS側のタイムゾーンをUTCに明示した15分定期実行とし、repo rootで`uv run --package alter-scheduler alter-scheduler`を1回だけ実行する。OSロック等で前回の実行中は次の起動を抑止し、DBの一意制約も併用する。停止中に逃した全スロットを復帰時に再生せず、現在の枠から再開する。

計画停止では定期実行を止め、Worker/Botが新規claimしないようにして進行中処理を制限時間内に終了させ、APIと中継を停止する。強制停止後もジョブや予約を手動で一括削除しない。復旧では時刻同期・回線・DB接続・migration版を確認し、失効/削除要求と課金/投稿の結果不明を照合する。失効leaseはDBジョブの規則で回収し、投稿結果不明や課金不明を自動再送/再課金/予約解放しない。確認後にAPI→Worker→Bot→定期実行を再開する。具体的な確認と照合のコマンドは[運用手順書](27-operations-runbook.md)に記載する。

### バックアップと復元（#33、#36）

Supabase Freeでマネージド自動バックアップは利用できる前提にしない。運営者は少なくとも稼働日ごと、およびmigration前にDBバックアップを取得し、PCディスク故障に備えて暗号化した別の保存先へ保管する。Auth/Storage/DBのどのデータが復元対象か、保持期限と削除/失効の再適用方法を #33/#36 で具体化し、公開前に復元演習を行う。鍵はDBバックアップと分けて暗号化保管する。バックアップの保存期間/削除は[運用契約](23-invitation-consent-operations.md)の公開ゲートに従う。

## 環境変数と秘密

| 対象 | 変数名 | 用途 / 公開可否 |
|---|---|---|
| Web | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL` | 公開可。秘密を`NEXT_PUBLIC_`へ置かない |
| Web server | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | SSRクライアント。公開可能な値だがサーバー設定にも保持 |
| API | `ALTER_ENVIRONMENT`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWT_AUDIENCE`, `WEB_URL`, `CORS_ORIGINS`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `PROFILE_ENCRYPTION_KEY_V1` | DB/OAuth/暗号鍵はPCのAPI専用環境ファイル/OS秘密管理。`CORS_ORIGINS`はCSVで明示 |
| Worker | `ALTER_MODE=live`, `LLM_ENABLED=true`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `MAX_DAILY_COST_USD` | LLMキーをWebに渡さない |
| Bot | `DATABASE_URL`, `DISCORD_BOT_TOKEN`, `WEB_URL`, `DISCORD_CLIENT_ID` | Bot tokenはPCのBot専用環境ファイル/OS秘密管理 |
| Scheduler（OS定期実行） | `ALTER_MODE=live`, `SCHEDULER_ENABLED=true`, `DATABASE_URL`, `SCHEDULER_BATCH_SIZE`, `MAX_DAILY_COST_USD` | 15分ごとに`uv run --package alter-scheduler alter-scheduler`を1回実行。LLMキー不要、既定無効 |
| Purge（OS定期実行） | `ALTER_MODE=live`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_STORAGE_SECRET_KEY` | 毎時`uv run --package alter-api alter-api-purge`を1回実行。管理鍵はAPIと同じPCの専用環境ファイル/OS秘密管理に置き、Scheduler・Worker・Botの環境ファイルに入れない（ADR-018） |
| Ops確認（OS定期実行） | `DATABASE_URL`, `MAX_DAILY_COST_USD` | `uv run alter-ops --env production --operator <担当> status`。LLMキー・管理鍵・Bot tokenは不要 |
| DB migration | `SUPABASE_DB_URL` | CI/管理者のみ。通常サービスに配らない |

### APIの環境別設定（#34、#35 と対応）

Preview/Productionでそれぞれ設定し、他の環境の値を流用しない。JWTの発行元を環境ごとに分けることで、PreviewのトークンをProductionのAPIが受け付けない。

| 変数 | local | Preview | Production |
|---|---|---|---|
| `ALTER_ENVIRONMENT` | `local`（既定） | `preview` | `production` |
| `SUPABASE_URL` | `http://127.0.0.1:54321` | Preview用Supabaseプロジェクト | 本番用Supabaseプロジェクト |
| `SUPABASE_JWT_ISSUER` | `http://127.0.0.1:54321/auth/v1` | `https://<preview-project>.supabase.co/auth/v1` | `https://<prod-project>.supabase.co/auth/v1` |
| `SUPABASE_JWT_AUDIENCE` | `authenticated` | `authenticated` | `authenticated` |
| `SUPABASE_SECRET_KEY` | `supabase status`のSecret | その環境のSecret Key（PCのAPI専用秘密管理） | その環境のSecret Key（PCのAPI専用秘密管理） |
| `CORS_ORIGINS` | `http://localhost:3000` | VercelのPreview用`NEXT_PUBLIC_SITE_URL`と完全一致 | VercelのProduction用`NEXT_PUBLIC_SITE_URL`と完全一致 |

`SUPABASE_SECRET_KEY`はAPI/運営CLIだけに置き、Web・Worker・Bot・Cronへ渡さない。パージ用の運営CLI（`alter-api-purge`、ADR-018）はこの「運営CLI」に含まれ、`SUPABASE_STORAGE_SECRET_KEY`とともにAPIと同じ管理下で使う。Schedulerの定期実行には渡さない。`CORS_ORIGINS`に`*`は指定できない（起動時に拒否）。

### APIドキュメントの公開範囲

`/openapi.json`はすべての環境で公開する（Vercelのスモーク確認とTypeScript生成に使い、APIの形だけで秘密は含まない）。Swagger UIの`/docs`とReDocの`/redoc`は`ALTER_ENVIRONMENT=production`のときだけ無効にし（404）、local/Previewでは従来どおり表示する。`ALTER_ENVIRONMENT`は`local`/`preview`/`production`以外の値を起動時に拒否し、綴りの誤りで本番の画面が開いたままにならないようにする。

`.env.example`には上の変数名・ダミー値・取得先・必須/任意を記載し、実値はGitに入れない。PCでは環境/プロセスごとに秘密を分け、実行ユーザーのみ読取可能な環境ファイル（Unixなら`chmod 600`）またはOS秘密管理へ置く。運用ファイルはリポジトリ外に保管し、コマンド引数・ログ・同期共有フォルダーへ秘密を出さない。API専用鍵をWorker/Bot/Schedulerの環境へ一括配布しない。Discord OAuth secretはコールバックを処理するAPIだけに置く。Supabase server secretは通常CRUDに使わず、Authユーザー削除・招待時のidentity照合の限定管理操作だけに使用し、専用環境変数`SUPABASE_SECRET_KEY`をAPIに限定する。

## 招待管理の運営手順（ADR-014、#7で実装）

招待者の一覧は環境変数・設定ファイルへ置かず、環境ごとのDBで管理する。sskoheiがAPI実行環境内の運営専用CLIから対象環境を確認し、本人を私的経路で照合して登録・一覧確認・identity追加・失効を行う。CLIは共通ドメイン処理とDB接続を使い、HTTPの管理エンドポイントは追加しない。資格情報はサーバー側のSecret管理から読み、Web・コマンド引数・ログへ渡さない。

初回OAuth後のID確認はSupabase Auth管理情報を使う。Auth照会に管理鍵が必要な場合はAPIの限定管理処理でのみ使用し、Worker/Bot/Cronへ配布しない。CLIの識別子入力方法・コマンド・失敗時の再実行手順は #7 で実装と同時に本節へ追記する。Preview/Productionの招待データをコピーせず、それぞれ本人照合して登録する。復元時は招待失効・削除を再適用してからAPI/Worker/Bot/Cronを再開する。

## デプロイ手順

1. Vercel Hobbyの用途/使用量、Supabase Freeのプロジェクト数/容量と休止条件を確認し、本番/Previewの別プロジェクト、Google/GitHub OAuth、Redirect URL、JWT signingとバックアップを設定する（#33、#35）。
2. CIでSQL migrationを検証し、Previewへ先行適用する。本番へのDDLはレビュー済みSQLを一方向適用し、下位互換migrationを先行する。
3. 手元PCの実行ユーザー・プロセス別秘密・OSサービス・UTC定期実行・HTTPS公開経路を設定し、API→Worker→Bot→Schedulerを起動する（#34）。SupabaseへのTLS/IPv4/プーラー接続をPCから検証する。[Supabase接続](https://supabase.com/docs/guides/database/connecting-to-postgres)。
4. Vercelへ公開HTTPS API URLを設定し、別端末からPreviewでE2E、ProductionでAuth callback・CORS・SSR疎通を確認する。Discordテストギルドへの投稿は対象/予算を確認して行う（#35、#37）。
5. PC停止/スリープ/回線断、独立プロセス再起動、lease回収、停止枠の非再生、課金/投稿結果不明の保留、期限内パージ、バックアップ復元を確認する（#36、#37）。
6. rollbackは定期実行・Worker/Botを停止してアプリを前版へ戻す。破壊的DDLは別リリースで実行する。復元時は失効/削除を再適用してから再開する。常時利用が必要になったら有料基盤への移行を再検討する。

### 自律交流ONを有効にする設定（#14・#16・#24）

送信先LLMが決まり同意文面を確定したら、同じ環境で次の3つを同時に設定する。Webの`ALTER_LLM_NAME`と`ALTER_LLM_DATA_POLICY_URL`（同意画面に表示）、APIの`ALTER_CONSENT_VERSION`（監査に記録する文面の版）。どれかが空の間は自律交流をONにできない（WebはONボタンを出さず、APIは409）。片方だけ設定すると「ONボタンは押せるがAPIが409」になるため、Preview/Productionそれぞれでそろえてから公開する。文面を変えたら版も更新する。

費用と無料枠は [費用試算](20-cost-estimation.md)。

### Claude Sonnetの採用設定（#14、PR #57レビュー対応）

API・Workerと明示的な評価CLIの秘密キー名は`ANTHROPIC_API_KEY`。
旧名`LLM_API_KEY`は使用しない。`LLM_MODEL=claude-sonnet-5-5`を指定し、
キーをWeb/Vercel/`NEXT_PUBLIC_*`へ渡さない。
#18のadapter・独立moderation・費用付き共通実行と提案APIは実装済み。
APIの提案またはWorkerの会話生成を有効化するとき、各サーバー側へ`ALTER_MODE=live`、`LLM_ENABLED=true`、
`ANTHROPIC_API_KEY`、`MAX_DAILY_COST_USD`（既定1.00）を設定する。
`LLM_ENABLED`は既定falseで、鍵だけでは有料通信を行わない。Webへ鍵/有効化変数を渡さない。
`LLM_MODEL`は表示用の固定採用値で、任意モデルへの切替スイッチではない。
Workerのclaim/保存・独立安全判定は#20で接続済み。lease90秒、heartbeat25秒を固定する。
`uv run --package alter-worker alter-worker`で起動し、`--once`で最大1件を処理する。
SIGTERM/SIGINT後は新規呼出を止め、既知費用を精算する。実品質の受け入れは#37で行う。
呼出は30秒/connect5秒で、HTTP/SDK内部の自動再試行はしない。
429/未課金一時障害はRetry-Afterを返し、課金不明ならllm_runsと残予約を保持する。
復旧時は#36で状態・usage_records・供給元利用量を照合し、確認せず再実行/解放しない。
1提案の最大予約は$0.012448で、1体の未完実行は1件まで。

Webの`ALTER_LLM_NAME`には`Claude Sonnet 5.5（Anthropic直接API）`を表示名として使う。
`ALTER_LLM_DATA_POLICY_URL`には確認済みの商用API向け保持説明
（https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data）を指定する。
これは学習へのopt-inやZDRの設定ではない。APIの`ALTER_CONSENT_VERSION`はdocs/23の同意文面を
確定してから同時に設定する。今回の修正では値を自動設定せず、空の場合のON拒否を維持する。

### アイコンStorage（#15）

migrationでprivate bucket `agent-icons`（PNGのみ/2 MiB）と`profile-private`を作成する。
APIにのみ`SUPABASE_STORAGE_SECRET_KEY`を設定する。Auth管理用キーをコードで流用しない。
未設定でもアイコンなしの人格APIは動作し、アップロード/署名のみ503になる。
キーはAPIのStorage操作に使えるプロジェクトSecretまたはservice_roleキーを秘密管理から設定する。
モデル/LLM用キーとは無関係。Web・配信JS・Worker・Botへ渡さない。

Previewではテスト画像でアップロード→作成時指定→取得→PATCH置換/解除を確認し、他人のprefix/
未登録パス/私的bucket/SVG/破損/超過画像が拒否されることを確認する。
署名URLの有効期限は300秒。Storage APIはローカル/CIではHTTP Mockで検証し、
実環境のStorage接続・キー権限の確認は#37の公開受け入れに記録する。

Webの環境別設定、callback/CORS登録、限定スモークと#35の完了記録は[Webデプロイ手順](25-web-deployment.md)を参照する。
