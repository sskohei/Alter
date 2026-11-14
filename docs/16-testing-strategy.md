# テスト・品質保証

## 必須ゲート

| 層 | 自動テスト | 主なケース |
|---|---|---|
| Frontend | Vitest + Testing Library、Mock API、Playwright | ログイン復帰、作成/承認、OFF、履歴閲覧、404/429、モバイル375px |
| API | pytest + FastAPI TestClient + PostgreSQL統合 | JWTの署名/iss/aud/exp、所有権、RLS直接アクセス拒否、OpenAPI schema |
| Worker | pytest + 偽LLM | 8ターン/時間/予算、注入文、出力不正、一時障害、重複実行、停止直後 |
| Scheduler | 固定時刻・DB統合 | 同時cronで予約1件、ブロック/未同意除外、日次上限 |
| Discord | discord.py境界のMock | 連携ID解決、管理者権限、投稿条件、429、送信結果不明 |
| Design | Figmaレビュー + axe + 手動 | コントラスト、キーボード順、スクリーンリーダー、PC/mobile整合 |

CIは本番LLMや本番Discordへアクセスしない。会話文章の完全一致ではなく、発言者順・schema・上限・禁則・保存/配信条件を検査する。プロンプトインジェクションの固定データセットを持ち、相手発言から秘密開示命令を実行しないことを確認する。DB統合テストはmigrationを空DBに適用して行い、RLSとFK/UNIQUEを検証する。OAuthの外部実接続、手元PC→SupabaseのTLS/プーラー接続と公開HTTPS経路→手元PC APIの疎通、Discord権限はステージングで最終確認する。

手元PC配置（#34〜#37）では、別端末の公開WebからAPIへ接続し、CORSの許可/拒否、OAuth callback、SSRからのAPI接続を確認する。PCの停止/スリープ/回線断でAPI失敗が表示され、ローカル値やMockへ自動切替しないことを確認する。PC再起動後のプロセス復帰、UTCの15分定期実行と多重起動抑止、Bot単独停止中の会話継続、PC全体停止後のlease回収、課金/配送結果不明の保留、停止期間の一括生成なし、期限内パージとDBバックアップ復元を検証する。文書変更だけでこれらを検証済みと扱わない。

## 招待・同意・削除の引き渡し（Issue #4）

[運用契約の受け入れケース](23-invitation-consent-operations.md)を #7/#9/#15/#16/#27/#28/#30/#31 と公開受け入れ #37 で検証する。Google/GitHubごとに招待済み200、招待外403、削除/停止403、判定障害503を確認し、直URL・直接API・別sub・同一メール・古いJWT・再OAuthでも迂回できないことを検査する。拒否時のプロフィール/ジョブ/LLM副作用は0件。各同意の初期OFF、文面版の記録、共有会話の双方非表示/派生データ削除、鍵交換/バックアップ復元はMock・合成データ・固定時刻で検証する。実装前の文書PRでは実行済みと扱わない。

招待DB＋運営CLI（ADR-014）は #7 で空DBからの追加migration、招待2表のanon/authenticatedによる直接CRUD拒否、sub/provider IDの一意性、Google/GitHubを1人と数えること、同時登録時の10人上限を検証する。CLI失効はAI停止/未配信取消/監査と同時確定し、途中失敗で全体がロールバックすることをDB統合テストで確認する。失効後のAPI拒否、DB/Auth障害時の503と副作用なし、CLIログ/監査への実ID・秘密混入なしも確認する。実装は`supabase/tests/invitations.test.sql`（pgTAP）と`tests/db/`（Python DB統合テスト。`ALTER_TEST_DATABASE_URL`設定時だけ実行し、CIのMigrationsジョブでローカルSupabaseに対して実行する。招待2表を空にするため使い捨てDB専用）。APIの共通認可と`GET/PATCH /users/me`は`tests/test_api_users.py`（DBなし: 未認証401、Mockモードで認可を迂回しない503、入力検証、OpenAPI）と`tests/db/test_users_api_db.py`（招待済み200・同時初回要求で1プロフィール、招待外/未登録identity/未対応provider/Auth消去済みの403とプロフィール未作成、失効・削除済みの403と削除状態を復活させないこと、Auth障害503、表示名の更新と422）で確認する。交流設定・ブロック・通知設定（#16）は`tests/test_api_settings.py`（未認証401・OpenAPI契約）と`tests/db/test_settings_api_db.py`（初期OFF、他人のAIは404、同意文面の版の監査、未承認・停止中・同意文面なしのON拒否409、入力不正422と保存値・監査が変わらないこと、予算の上限ルール、OFF/ブロックでqueuedの会話とジョブだけ取り消すこと、ブロックの冪等性と相手IDを監査に残さないこと、通知の初期値と更新）で確認する。会話・通知API（#21）は`tests/test_api_activity.py`（未認証401・OpenAPI契約・cursor）と`tests/db/test_activity_api_db.py`（自分の会話だけ・新しい順・ページング、`cost_usd`は自分のchargeだけで実行中はnull、allowedの発言だけ、他人の会話は404、削除要求者の会話と通知を隠す、作成から90日を過ぎた会話・通知を一覧から外しID指定と既読を404にする、完了通知の冪等性と通知設定の尊重、既読の冪等性）で確認する。アカウント削除の受付（#30 前半）は`supabase/tests/deletion_requests.test.sql`（RLS・直接CRUD拒否、進行中1件、status/完了日時/error_codeのCHECK、FKなし）、`tests/test_api_deletion.py`（未認証401・Mockで迂回しない503・OpenAPI契約・再認証の時刻判定・`amr`の厳密な読み取り）、`tests/db/test_deletion_api_db.py`（5分以内のOAuth以外は403で副作用なし、confirm必須、1トランザクションでの停止・取消・招待失効・ジョブ投入・監査、以後の業務API 403と状況確認200、相手の画面から共有会話が消えること、再送と同時要求で1件、途中失敗で全部戻ること、招待外・失効済みの拒否、他人の状況は見えないこと）で確認する。#30/#37では招待識別情報の期限内パージ、パージ後の再OAuth拒否、復元時の失効再適用を検証する。Discord本人連携（#26）は`supabase/tests/discord.test.sql`（Discord 7表のRLS・直接CRUD拒否、public全表のRLS有効とポリシー0件、トークン列がないこと、snowflake形式、有効連携の部分UNIQUE、stateのハッシュ形式と10分上限、1ギルド1投稿先、outboxのstatusと一意性、削除の連鎖とRESTRICT）、`tests/test_api_discord.py`（未認証401・OpenAPI契約・OAuthクライアントの失敗時にトークンや秘密を例外に含めないこと）、`tests/db/test_discord_api_db.py`（stateのハッシュ保存・使い捨て・期限切れ・別の本人・件数上限、失敗時もstateを消費すること、連携の衝突409、トークンがDB/応答/監査に残らないこと、解除で未送信DM・投稿・同意だけ取り消すこと、解除の冪等性、招待失効・削除要求の共通処理でも未送信だけ取り消すこと、未設定503とDiscord障害503）で確認する。Discord Bot（#27）は`tests/test_discord_bot.py`（Intentsが`guilds`だけ、コマンド一覧、`/server`の既定権限とギルド限定、Botの投稿権限の判定、live時のトークン必須）、`tests/db/test_discord_commands_db.py`（未連携・連携失効・削除要求・招待失効の拒否、公開欄だけのprofile、本文を出さないhistoryと件数上限・90日・削除要求者の除外、Webと同じ規則のpause/resume、setupの管理権限とBot権限の確認、投稿先変更での同意と未投稿の取消、publishの説明と確認・ギルド単位の同意と取消、ギルド離脱）で確認する。会話投稿・DM通知（#28）は`tests/test_discord_delivery.py`（エスケープ・AIラベル・2,000文字の分割・スレッド名・リンクだけのDM、失敗の分類、偽のDiscordで投稿が権限不足・拒否・送信結果不明・途中失敗を正しく記録し、失敗した書き込みを自分で送り直さないこと、DM拒否を再試行しないこと）、`tests/test_discord_rest.py`（書き込みのメンション無効・埋め込みなし、5xx・接続断・タイムアウトで送り直さないこと、429だけ`retry_after`に従って待って送り直し上限で止めること、例外に本文・トークンを含めないこと）と`tests/db/test_discord_delivery_db.py`（作成の冪等性と取消済みを戻さないこと、投稿とDMの各条件（招待の有効を含む）を作成時と取り出し時の両方で確認すること、投稿先が2つ以上なら作成時も取り出し時も送らず保留すること、再連携した別のDiscordユーザーへ古いDMを送らないこと、同時の取り出しで同じ行を取らないこと、再試行の間隔と打ち切り、結果不明を再送しないこと、止まった送信中の回収、公開OFF・ブロック・DM OFFでの取消、送信ループの最後までの流れ）で確認する。

## リリース受け入れ

10アカウントで同時に作成・交流ON/OFF・Web閲覧・Discord公開を試す。非参加者が会話IDを知っても読めないこと、停止後に新規LLM呼出が始まらないこと、Bot停止中も会話が完了し復帰後に許可済み通知が1回だけ配信されることを確認する。LLM課金の二重発生可能性は監視で検知し、費用上限が最終防壁となる。SLO初期目標は予約から交流完了まで15分以内、API通常読み取りp95 1秒以内（外部障害を除く）。

## 現在のCI（Issue #3）

`.github/workflows/ci.yml`は`main`向けの全PR（文書のみも含む）、`main`へのpush、手動実行で起動する。必須チェック名は**`CI required`**。この集約ジョブは次の5ジョブがすべて`success`の場合だけ成功し、失敗・キャンセル・スキップを成功扱いしない。パスによるworkflowのスキップは設けない。

| ジョブ | 現在の検査 | ローカル再現 |
|---|---|---|
| Python | Ruff lint/format、strict mypy、pytest | READMEのPython検証コマンド |
| Web | ESLint、TypeScript、Vitest + Testing Library、Next本番build、Playwright Chromium（PC/375px） | `pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm test:e2e` |
| API contract | FastAPIからOpenAPIとTypeScript schemaを再生成し、生成物のGit追跡と再生成差分を検査 | `pnpm api:generate && git diff --exit-code -- packages/api-client/openapi.json packages/api-client/src/schema.d.ts` |
| Migrations | 空のローカルSupabase PostgreSQL 17を起動し、全migrationを順に適用、public schemaのSQL lint | `pnpm exec supabase db start` → `pnpm exec supabase db reset --local --no-seed` → `pnpm exec supabase db lint --local --schema public --fail-on error` |
| Secrets | checksum固定のGitleaks 8.30.1で取得済みGit履歴全体を検査、検出値を100%伏せて失敗 | 同じバージョンの`gitleaks git --redact=100 --no-banner --log-opts="--all" .` |

Playwrightの初回準備は`pnpm --filter web exec playwright install --with-deps chromium`。E2Eは`pnpm build`後のWebを自動起動し、`/health`通信をPlaywrightでMockする。VitestもfetchをMockする。API/Worker/Bot/Schedulerは`ALTER_MODE=mock`、`DISCORD_ENABLED=false`で検査し、本番キーやActions Secretsは渡さない。`.env`の作成・読み込みはCIでは行わない。GitHub Tokenはcontentsのreadのみ、checkoutの資格情報保存を無効化し、Actionsはcommit SHAで固定する。

migration検査は使い捨てのCI環境向け。`db reset`はローカルDBを削除するため、手元では既存データを保持する必要がないDBで実行する。現時点の`supabase/migrations/`にはSQLがないので、空DBの初期化・reset/lintの成功を検査し、ジョブのsummaryにその範囲を表示する。最初のSQL追加からmigration適用検査は自動的に有効となる。次の検査は対応する実装のPRで追加する。

- 認証・業務API: JWT/所有権、非参加者拒否、404/429のMock、認証画面のVitest/E2E。
- 最初のテーブル・RLS: anon/authenticatedの直接アクセス拒否、FK/UNIQUE、両参加者の同意をDB統合テストまたはpgTAPで検査し、Migrationsジョブへ追加。
- Worker/Scheduler/Discord: 偽LLM・固定時刻・通信MockとDBを組み合わせ、予算・停止・同時実行・再試行を検査。

2026-11-02時点でGitHub標準secret scanning / push protectionの有効化APIは`422: Secret scanning is not available for this repository`となった。Gitleaksを全PRの必須CIに含めて代替する。これはpush前のブロックを提供しないため、秘密を発見した場合は直ちに失効・再発行し、履歴やログに残った値も対処する。標準機能が利用可能になった時点で追加有効化する。


Scheduler（#19）は`tests/test_scheduling.py`と`tests/db/test_scheduling_db.py`で検証する。
UTC slot、seed再現性、mock/無効時のDB未接続、liveの1回実行/終了/障害伝播、
同時Cronの同ペア1予約、異なるペアの全体費用競合、予算不足の原子的rollback/次候補、
承認/双方ON/削除/招待/片側ブロック/日次枠/同時1会話/未確定LLM実行、
採用モデルの双方費用予約と承認版スナップショット、Discord不同意での非公開交流、
双方/片側のDiscord公開同意スナップショット、APIとSchedulerの競合時のロック順を含む。
DBテストは使い捨てのローカルSupabaseでのみ実行し、外部LLM/Discordには接続しない。


Worker（#20）は`tests/test_worker.py`と`tests/db/test_conversation_db.py`で検証する。
独立入口のmock/無効ゲート・資格情報検査・SIGTERM、25秒heartbeat、同時Workerと90秒lease失効、
保存済みターンからの再開、未完ターンの再呼出禁止、会話のない古いジョブの除外、古いWorkerの費用のみの記録、
8発言/should_end/5分/予算、停止/ブロック/削除/人格失効、schema/独立判定の拒否、
未課金再試行/バックオフ/上限と課金済みの再生成禁止、取消予約解放、event/通知の冪等性を含む。
Sonnet adapterはHTTP Mockで生成/独立判定とcount_tokens後の停止を検証し、実課金はしない。


記憶（#22）は`tests/db/test_memory_db.py`で、同時consumer／失敗rollbackと再起動、最大3候補・出典・hash、
秘密／PII／命令の除外、上位5件・相手・保持期限、次回Workerの実プロンプト、生成中／count_tokens後の本人削除、
別出典の同じ削除hashの要約抑止、直接SQLの会話パージ後の関係再集計を確認する。
既存`test_memory_api_db.py`の正式API認可・再送・挿入ガードも維持する。
`memory_suppression.test.sql`は3表のRLS・ポリシー0件・直接CRUDと全テーブル権限の拒否、
AI／会話CASCADE・無関係な記録の保持・パージtriggerの権限を検証する。
