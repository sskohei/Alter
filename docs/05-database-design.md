# Supabase PostgreSQL設計

## 規約

DDLの正本は`supabase/migrations/*.sql`。時刻は`TIMESTAMPTZ` UTC、IDは`UUID DEFAULT gen_random_uuid()`、Discord snowflakeは`TEXT`、金額は`NUMERIC(12,6)` USD。全業務テーブルは`public`スキーマ、`created_at NOT NULL DEFAULT now()`を共通列とする。以下で`!`はNOT NULL、`?`はNULL可。`id uuid! PK`は明記省略時の共通主キー。`updated_at`は編集可能テーブルのみ必須。JSONはPydanticでスキーマ検証し、無制限の任意キーを認めない。すべてのFKに索引を付ける。

`profiles.id`は`auth.users.id`と同値。JWT・招待・停止/削除状態の検証を通った最初の`GET /users/me`でAPIが`INSERT ... ON CONFLICT DO NOTHING`により作成し、認証イベントの配送順に依存しない。AuthのIDをメールで推測・結合しない。招待外・削除要求済みの利用者をこの初期化で作成/復活させない。許可リストはDBの`invitations`・`invitation_identities`を正本とし、APIと運営専用CLIからのみ扱う。[招待契約](23-invitation-consent-operations.md)に従う。

## テーブル（型・制約・索引・削除）

| テーブル・目的 | カラム（共通 `created_at` 以外） | 主な制約・索引 | 削除・保持 |
|---|---|---|---|
| `invitations` 招待 | `id uuid! PK`, `auth_user_id uuid!`, `status varchar(16)!`, `revoked_at timestamptz?`, `updated_at timestamptz!` | `UNIQUE(auth_user_id)`、`status IN (active,revoked)`、状態と`revoked_at`の整合CHECK、`status`索引 | Auth/プロフィールから独立。失効後30日以内、削除要求後30日以内の早い期限でパージ |
| `invitation_identities` 許可identity | `id uuid! PK`, `invitation_id uuid! FK invitations(id)`, `provider varchar(16)!`, `provider_user_id text!` | `UNIQUE(provider,provider_user_id)`、`UNIQUE(invitation_id,provider)`（1人の招待に同じプロバイダーは1つ。`invitation_id`索引を兼ねる）、`provider IN (google,github)`、IDは空・前後空白不可 | 招待削除でCASCADE、実IDを監査へ複製しない |
| `profiles` 利用者 | `id uuid! PK FK auth.users(id)`, `display_name varchar(80)!`, `web_notifications bool! DEFAULT true`, `discord_dm_notifications bool! DEFAULT false`, `onboarded_at timestamptz?`, `deleted_at timestamptz?`, `updated_at timestamptz!` | `id UNIQUE`; `deleted_at`索引 | Auth削除でCASCADE。削除要求後30日以内にパージ |
| `agents` AI本体 | `id uuid! PK`, `owner_id uuid! FK profiles(id)`, `name varchar(80)!`, `icon_path text?`, `status varchar(16)!`, `approved_profile_version int?`, `updated_at timestamptz!` | `UNIQUE(owner_id)`（MVP1体）、`status IN (draft,active,paused,deleted)` | 所有者削除時に会話先行削除、後にCASCADE |
| `agent_profiles` 人格版 | `id uuid! PK`, `agent_id uuid! FK agents(id)`, `version int!`, `personality text!`, `values_text text!`, `interests text[]!`, `hobbies text[]!`, `strengths text[]!`, `weaknesses text[]!`, `speaking_style text!`, `interaction_policy text!`, `shareable_info text!`, `private_info_ciphertext bytea?`, `private_info_key_version smallint?`, `restrictions text!`, `approved_at timestamptz?` | `UNIQUE(agent_id,version)`、`version>0`。承認済み版は不変 | AI削除でCASCADE。私的文をログに出さない |
| `agent_settings` 交流設定 | `agent_id uuid! PK FK agents(id)`, `autonomy_enabled bool! DEFAULT false`, `frequency_per_day smallint! DEFAULT 1`, `daily_budget_usd numeric(12,6)! DEFAULT 0.20`, `max_session_usd numeric(12,6)! DEFAULT 0.05`, `web_visibility varchar(16)! DEFAULT 'participants'`, `discord_publish bool! DEFAULT false`, `updated_at timestamptz!` | 頻度0〜2、予算>=0。MVPの`web_visibility='participants'` | AI削除でCASCADE |
| `conversations` 交流 | `id uuid! PK`, `status varchar(16)!`, `topic text?`, `summary text?`, `visibility varchar(16)!`, `started_at timestamptz?`, `ended_at timestamptz?`, `profile_versions jsonb!`, `job_id uuid? UNIQUE`, `cost_usd numeric(12,6)! DEFAULT 0` | `status IN (queued,running,completed,failed,cancelled)`、`started_at DESC` | 90日保持。削除要求では関連分を早期パージ |
| `conversation_participants` 参加者 | `conversation_id uuid! FK conversations(id)`, `agent_id uuid! FK agents(id)`, `ordinal smallint!`, `discord_consent bool!`, `web_consent bool!` | 複合PK `(conversation_id,agent_id)`、`UNIQUE(conversation_id,ordinal)`、ordinal 1/2 | 会話削除でCASCADE、AI削除はRESTRICT |
| `messages` 発言 | `id uuid! PK`, `conversation_id uuid! FK conversations(id)`, `agent_id uuid! FK agents(id)`, `turn_no smallint!`, `content text!`, `moderation_status varchar(16)!`, `token_input int!`, `token_output int!` | `UNIQUE(conversation_id,turn_no)`、`turn_no BETWEEN 1 AND 8` | 会話削除でCASCADE、90日 |
| `memories` 長期記憶 | `id uuid! PK`, `agent_id uuid! FK agents(id)`, `source_conversation_id uuid? FK conversations(id)`, `text text!`, `importance smallint!`, `dedupe_key text!`, `status varchar(16)!`, `updated_at timestamptz!` | `UNIQUE(agent_id,dedupe_key)`、`importance BETWEEN 1 AND 5`、`(agent_id,status,importance)` | 本人削除は即時、通常180日見直し。会話削除時は記憶も削除 |
| `agent_relationships` AI関係 | `agent_low_id uuid! FK agents(id)`, `agent_high_id uuid! FK agents(id)`, `conversation_count int!`, `last_met_at timestamptz?`, `shared_interests text[]!`, `updated_at timestamptz!` | 複合PK、`agent_low_id < agent_high_id`、件数>=0 | どちらかのAI削除でCASCADE |
| `agent_blocks` ブロック | `blocker_agent_id uuid! FK agents(id)`, `blocked_agent_id uuid! FK agents(id)` | 複合PK、自己ブロック禁止 | どちらか削除でCASCADE |
| `notifications` Web通知 | `id uuid! PK`, `profile_id uuid! FK profiles(id)`, `kind varchar(32)!`, `conversation_id uuid? FK conversations(id)`, `body jsonb!`, `read_at timestamptz?`, `dedupe_key text!` | `UNIQUE(profile_id,dedupe_key)`、`(profile_id,created_at DESC)` | 90日。会話削除時関連通知削除 |
| `discord_connections` 本人連携 | `id uuid! PK`, `profile_id uuid! FK profiles(id)`, `discord_user_id text!`, `connected_at timestamptz!`, `revoked_at timestamptz?` | 有効行に`UNIQUE(profile_id)`・`UNIQUE(discord_user_id)`部分索引 | 解除時失効・トークン削除、監査用非秘密メタデータ30日 |
| `discord_oauth_states` OAuth CSRF防止 | `id uuid! PK`, `profile_id uuid! FK profiles(id)`, `state_hash text!`, `expires_at timestamptz!`, `used_at timestamptz?` | `UNIQUE(state_hash)`、`expires_at`索引 | 使用後即削除、未使用も10分で削除 |
| `discord_guilds` ギルド | `id text! PK`, `admin_discord_user_id text!`, `enabled bool!`, `created_at timestamptz!`, `updated_at timestamptz!` | 管理者ID索引。Bot離脱時`enabled=false` | 離脱時無効化、配送履歴の保持終了後に削除（最大90日） |
| `discord_channels` 許可チャンネル | `id text! PK`, `guild_id text! FK discord_guilds(id)`, `enabled bool!`, `configured_by text!`, `created_at timestamptz!` | 有効行の`guild_id`部分UNIQUE（MVPは1ギルド1投稿先） | 配信履歴保持中は削除せず無効化 |
| `agent_discord_consents` ギルド別公開同意 | `agent_id uuid! FK agents(id)`, `guild_id text! FK discord_guilds(id)`, `consented_at timestamptz!`, `revoked_at timestamptz?` | 複合PK `(agent_id,guild_id)`、有効行索引 | AIまたはギルド削除でCASCADE。解除後は未配信取消 |
| `discord_deliveries` 投稿outbox | `id uuid! PK`, `conversation_id uuid! FK conversations(id)`, `channel_id text! FK discord_channels(id)`, `status varchar(16)!`, `discord_thread_id text?`, `discord_message_id text?`, `attempts smallint!`, `next_attempt_at timestamptz!`, `last_error text?` | `UNIQUE(conversation_id,channel_id)`、`(status,next_attempt_at)` | 会話削除でCASCADE。投稿済みIDは会話保持期間と同じ |
| `discord_dm_deliveries` 個別通知outbox | `id uuid! PK`, `notification_id uuid! FK notifications(id)`, `discord_user_id text!`, `status varchar(16)!`, `discord_message_id text?`, `attempts smallint!`, `next_attempt_at timestamptz!` | `UNIQUE(notification_id)`、`(status,next_attempt_at)` | 通知削除でCASCADE。最大90日 |
| `usage_records` 費用台帳 | `id uuid! PK`, `agent_id uuid? FK agents(id)`（新規記録は必須。NULLはAI削除後の匿名化済み記録だけ）, `conversation_id uuid? FK conversations(id)`, `provider text!`, `model text!`, `input_tokens int!`, `output_tokens int!`, `estimated_usd numeric(12,6)!`, `actual_usd numeric(12,6)?`, `kind varchar(16)!`, `idempotency_key text!` | `UNIQUE(idempotency_key)`、`(agent_id,created_at)`、`(created_at)`、`kind IN (reserve,charge,release)` | 12か月保持後集計・削除。AI削除では匿名化して保持 |
| `background_jobs` 永続ジョブ | `id uuid! PK`, `kind varchar(32)!`, `dedupe_key text!`, `payload jsonb!`, `status varchar(16)!`, `run_at timestamptz!`, `lease_until timestamptz?`, `attempts smallint!`, `last_error text?`, `updated_at timestamptz!` | `UNIQUE(kind,dedupe_key)`、`(status,run_at)` | 完了後30日、失敗後90日 |
| `audit_events` 監査 | `id uuid! PK`, `actor_profile_id uuid?`, `action varchar(64)!`, `resource_type text!`, `resource_id text!`, `metadata jsonb!` | `(actor_profile_id,created_at DESC)` | 180日、秘密本文・トークン禁止 |
| `deletion_requests` 削除追跡 | `id uuid! PK`, `profile_id uuid!`, `status varchar(16)!`, `requested_at timestamptz!`, `completed_at timestamptz?`, `error_code text?` | `UNIQUE(profile_id)`（進行中） | 完了後30日、PIIなしで集計 |

`conversations.job_id`のFKは循環参照を避けて`background_jobs(id)`へ後付け。`background_jobs.status`は`queued,running,retry_wait,completed,failed,cancelled`、`discord_deliveries`と`discord_dm_deliveries`のstatusは`pending,sending,sent,needs_review,cancelled`に制限する。全テキスト長・配列件数はAPIとCHECKで制限する。`private_info`はAPIで暗号化してからDBへ保存し、復号は本人の編集要求だけに限定する。画像はSupabase Storageのprivate bucketに置き、公開用アイコンだけ署名URLまたは公開bucketへ複製する。

外部キーの削除動作は表の「削除・保持」をDDLで明示する。`memories.source_conversation_id`と`notifications.conversation_id`は会話削除時CASCADE、`discord_deliveries.channel_id`は配信履歴保持中の誤削除を避けるためRESTRICT。`private_info_ciphertext`と`private_info_key_version`は両方NULLまたは両方非NULLのCHECKを置く。配列の省略値は空配列とする。

承認済み人格版は、識別子・承認日時を含めて不変とする。ただし既存の非公開欄の鍵ローテーションでは、暗号文と鍵版だけを別の非NULL鍵版へ更新できる。承認後の非公開欄の追加・削除や同じ鍵版での内容変更は拒否し、内容を変える場合は新しい人格版を作る。DBは暗号文の平文同一性を判定できないため、管理APIの再暗号化処理で復号前後の一致を検証する。

## 招待テーブルの運用制約（ADR-014、実装は #7）

`invitations.auth_user_id`はSupabaseの`sub`と同値だが、Auth削除で失効記録が消えたりプロフィール作成を前提にしたりしないよう、`auth.users`/`profiles`へのFKを設けない。登録時にAuth側で実在・identityを検証し、削除はアプリのパージ処理で管理する。`active`は`revoked_at IS NULL`、`revoked`は`revoked_at IS NOT NULL`とする。有効化は本人照合・1つ以上の許可identity・10人枠確認と同一トランザクションで行う。枠の確認は登録/再参加の全経路で共通のトランザクションロックを取り、失効済み行を自動有効化しない。

招待2表のmigration（`20261108180000_invitations.sql`）・制約/RLSテスト・運営CLIは #7 で追加した。10人枠はドメイン処理とDBトリガーの両方で共通ロック（`lock_invitation_capacity()`）の下で確認し、有効な招待のidentity必須はコミット時の制約トリガーで、失効の不可逆性・識別子の不変性はトリガーで保証する。#6の既存migrationは変更しない。テーブル未適用や不整合・照会障害は認可エラー503として業務処理を止める。保持期限・CLI操作・失効と停止の原子性は[招待契約](23-invitation-consent-operations.md)に従う。

## 会話・ジョブ・記憶の運用制約（#13）

migrationは`20261109220000_conversations_jobs_memories.sql`、受け入れテストは`supabase/tests/conversations_jobs_memories.test.sql`。上表で値が未定義だった項目は次のとおり確定した（後続Issueで変える場合はここを更新する）。

- `messages.moderation_status IN (allowed,blocked)`。発言者は`(conversation_id,agent_id)`の複合FKで`conversation_participants`を参照し、参加者以外の発言を拒否する（`agents`への単独FKの代わり）。本文は空白不可・4000字以内。
- `conversations.visibility='participants'`固定（MVP）。`topic`200字・`summary`4000字以内、`ended_at >= started_at`、`profile_versions`はJSONオブジェクト。
- `memories.status IN (active,archived)`（本人削除は行削除、`archived`は180日見直し用）。本文1000字以内。
- `background_jobs.kind`と`notifications.kind`は値の一覧を固定せず、形式`^[a-z][a-z0-9_.]{0,31}$`だけを検査する。具体的な種類は利用するIssue（#19〜#21・#28・#30）で決める。`last_error`は500字以内の固定コードとし、例外本文や会話本文を入れない。
- `background_jobs`は`status='running'`のときだけ`lease_until`を持つ（CHECK）。`payload`・`notifications.body`はJSONオブジェクト。
- `agent_relationships.shared_interests`は20件以内。各`dedupe_key`/`idempotency_key`は空白不可・200字以内。

削除動作: 会話削除で参加者・発言・通知・出典の記憶をCASCADE。参加者の`agent_id`はRESTRICTで、会話が残るAIは削除できない（アカウント削除は会話→記憶→通知→AIの順）。`conversations.job_id`と`usage_records.conversation_id`は保持期間が違う（ジョブ30〜90日・費用12か月・会話90日）ためSET NULL。`memories.agent_id`・関係・ブロックはAI削除でCASCADE。`usage_records`は12か月保持し、運営全体の日次費用を減らさないためAI削除でも行を残す。`agent_id`はSET NULLとし、同時にトリガーで`conversation_id`をNULL、ID を含み得る`idempotency_key`を`anonymized:<行id>`へ置き換えて再識別できる紐付けを外す（docs/15）。匿名化した行は別のAI・会話へ付け直せず、新規記録は`agent_id`必須。

Scheduler/Workerが使う索引とトランザクション境界:

| 処理 | 条件・ロック | 索引 |
|---|---|---|
| 予約（Cron） | 対象AIの`agents`行を`FOR UPDATE`→当日の`usage_records`（確定＋未解放予約）と進行中参加を確認→`background_jobs`と`conversations`・参加者・`reserve`を同一トランザクションで作成。重複は`(kind,dedupe_key)`で拒否 | `usage_records(agent_id,created_at)`、`conversation_participants(agent_id,conversation_id)` |
| 取得（Worker） | `status IN (queued,retry_wait) AND run_at<=now()`を`FOR UPDATE SKIP LOCKED LIMIT 1`→同一トランザクションで`running`・`lease_until=now()+90s`・`attempts+1` | 部分索引`background_jobs(run_at) WHERE status IN (queued,retry_wait)` |
| lease切れ再取得 | `status='running' AND lease_until<now()`を同様に条件付きUPDATE | 部分索引`background_jobs(lease_until) WHERE status='running'` |
| 発言保存 | `(conversation_id,turn_no)`を冪等キーに発言と`usage_records`を同一トランザクションで保存 | `messages(conversation_id,turn_no)` UNIQUE |
| 完了 | 会話`completed`・要約・通知・関係回数・配信outboxを同時確定。通知は`(profile_id,dedupe_key)`で重複しない | `notifications(profile_id,created_at DESC)` |

同じペアの並行セッション禁止はDB制約では表さず、予約時の行ロックと`dedupe_key`で防ぐ（#19で受け入れ）。

## RLSと接続ロール

全`public`業務テーブルでRLSを有効にし、`anon`の読み書きを禁止する。`authenticated`には直接DML権限を与えず、MVPでは**すべての表でRLSポリシー0件（default deny）**とする。対象は上表の全24表で、`invitations`・`invitation_identities`・`profiles`・`agents`・`agent_profiles`・`agent_settings`・`conversations`・`conversation_participants`・`messages`・`memories`・`agent_relationships`・`agent_blocks`・`notifications`・`discord_connections`・`discord_oauth_states`・`discord_guilds`・`discord_channels`・`agent_discord_consents`・`discord_deliveries`・`discord_dm_deliveries`・`usage_records`・`background_jobs`・`audit_events`・`deletion_requests`。将来、公開プロフィール投影を追加する場合は別VIEWと限定SELECTポリシーを設計する。`postgres`等の管理権限はRLSを迂回し得るため、APIはJWT検証と所有権SQL条件を必須とする。Worker/Botは別の最小権限DB資格情報を持つのが正式版目標。MVPでは管理接続を使う場合、秘密をRenderだけに置き、各操作の`WHERE owner_id=...`と参加者検証をテストで必須とする。新規関数の既定権限は、スキーマ単位の付与を外すことに加え、グローバルのPUBLIC EXECUTEも取り消す。RLSをAPI側の認可代替と数えない。[Supabase RLS資料](https://supabase.com/docs/guides/database/postgres/row-level-security)、[JWT資料](https://supabase.com/docs/guides/auth/jwts)。

招待2表の参照・変更はAPIの認可/限定管理処理と運営CLIに限定し、Worker/Bot/Cronは停止状態を業務表経由で確認する。運営CLIは招待・停止/取消・監査に必要な処理だけを共通ドメイン経由で実行する。

論理アクセス権はAPIが全24表のうち公開REST操作に必要な行、Workerが`agents`/人格/設定/会話/発言/記憶/関係/通知/費用/ジョブ/配信、Cronが`agents`/設定/ブロック/関係/費用/会話/ジョブ、BotがDiscord連携/ギルド/同意/配信/通知/設定に限定する。ブラウザの`anon`と`authenticated`は全24表へ直接アクセス不可。MVPで同じ管理DB資格情報を共有する場合も、サービス層のリポジトリを分けてこの論理権限だけを呼べるようにする。

API/Worker/BotはSupabaseの接続プーラーを使う。短いトランザクションはtransaction mode、セッション状態が要る処理はsession modeを検証する。`SKIP LOCKED`はトランザクション内で使い、セッションロックに依存しない。マイグレーションはsession/direct接続で実行。接続数とIPv4経路はデプロイ時に検証する。[Supabase接続資料](https://supabase.com/docs/guides/database/connecting-to-postgres)、[Renderからの接続資料](https://render.com/articles/how-to-connect-a-render-service-to-supabase-postgres)。

## トランザクション境界

- 招待登録: 共通ロックの下で有効人数と本人/identityを確認し、招待・identity・監査を同時確定する。失効はAI停止・未配信取消・監査と同時確定し、Auth削除はコミット後に行う。
- 予約: 対象AIを行ロックし、双方の当日利用量と同時実行を確認して`background_jobs`と`conversations`を同じトランザクションで作る。
- 発言: `(conversation_id,turn_no)`を冪等キーとして保存。使用量は同一トランザクションに追加。
- 完了: `status=completed`、要約、通知、許可された配信outboxを同時確定する。
- 削除: まず新規交流と配信を停止し、対象会話→記憶→通知→AI→プロフィール→Authユーザーの順で削除する。外部Discord投稿の削除試行は別ジョブで記録する。会話CASCADE前に配送IDを本文なしで削除ジョブへ引き渡す。要求時から関連会話・派生記憶/通知を双方の参照対象外とし、招待失効後にAuthを削除する。保持期限と30日以内パージ・匿名化・復元時の扱いは[運用契約](23-invitation-consent-operations.md)に従う。

[ER図](diagrams/er-diagram.md)・[認可設計](06-authentication-authorization.md)を参照。
