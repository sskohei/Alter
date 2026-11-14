# REST API契約 v1

基点`/api/v1`、JSON、UTC ISO 8601、UUID。OpenAPIの`operationId`は固定し、FastAPIから`/openapi.json`を生成して`packages/api-client`に型・クライアントを生成する。エラー共通形は`{"error":{"code":"FORBIDDEN","message":"...","request_id":"uuid"}}`。一覧は`limit`（1〜50、既定20）と不透明`cursor`、返却は`{items,next_cursor}`。全書込はCSRF/CORSとBearer検証を通し、削除・開始・公開操作には`Idempotency-Key`を受け付ける。共通エラー: 401認証なし/期限切れ、403権限不足、404対象なし、409競合、422入力不正、429制限、503外部依存不可。利用画面の略号は [画面仕様](12-frontend-screens.md) のURLを指す。

## 招待・停止・削除の共通認可

`/health`を除く一般業務APIは[招待契約](23-invitation-consent-operations.md)の共通認可を通す。無効JWTは401 `UNAUTHENTICATED`、有効JWTでも招待外は403 `INVITATION_REQUIRED`、停止/削除要求済みは403 `ACCOUNT_INACTIVE`、招待判定障害は503 `AUTHORIZATION_UNAVAILABLE`。`GET /users/me`も例外ではなく、拒否時にプロフィールを作成しない。削除要求の本人向け状況確認は最小限の状態だけを返す専用経路とし、#30/#31でPydantic/OpenAPIを確定する。

招待照合はDBの`invitations`・`invitation_identities`を参照する。DB障害・データ不整合・Auth照会障害は503で拒否する。運営専用CLIは共通ドメイン処理を利用し、公開APIに招待管理操作や招待一覧を追加しない（ADR-014）。

#7 で共通基盤・JWT検証、招待DB・運営専用CLI・招待照合のドメイン処理（`alter_domain.invitations.evaluate_access`）、APIの共通認可（`alter_api.business.BusinessConnection`）と`GET/PATCH /users/me`を実装した。停止状態は現時点では`profiles.deleted_at`と招待の失効で判定し、`deletion_requests`の進行中要求は表を追加する #30 で同じ判定に加える。業務エンドポイントの追加時にPydantic/OpenAPIと生成TypeScriptを同時更新する。

## スキーマ（公開API）

| 名称 | 必須フィールド | 任意フィールド・制約 |
|---|---|---|
| `AgentDraftInput` | `name`, `personality`, `values_text`, `interests`, `speaking_style`, `interaction_policy`, `shareable_info`, `restrictions` | `private_info`（空が既定）, `hobbies`, `strengths`, `weaknesses`, `icon_path`。テキスト各2000字、配列各20項目 |
| `Agent` | `id`, `name`, `status`, `approved_profile_version`, `created_at` | `icon_url`, `settings`。`private_info`は通常応答に含めない |
| `ProfileRevision` | `version`, `personality`, `values_text`, `interests`, `speaking_style`, `interaction_policy`, `shareable_info`, `restrictions`, `approved_at` | `hobbies`, `strengths`, `weaknesses`。私的欄は本人編集APIだけ |
| `AgentSettingsPatch` | 少なくとも1項目 | `autonomy_enabled`, `frequency_per_day`(0〜2), `daily_budget_usd`, `max_session_usd`, `discord_publish`, `web_visibility`(MVPはparticipants固定) |
| `ConversationSummary` | `id`, `status`, `partner`, `started_at`, `summary`, `visibility` | `relationship_change`, `cost_usd` |
| `ConversationDetail` | `ConversationSummary`の各項目、`participants`, `messages` | `messages[]`は`id,agent_id,turn_no,content,created_at`。非公開欄は返さない |
| `Memory` | `id`, `text`, `importance`, `source_conversation_id`, `created_at` | `status` |
| `Notification` | `id`, `kind`, `body`, `created_at`, `read_at` | `conversation_id` |
| `DiscordConnection` | `connected`, `discord_user_id` | `guilds[]`。トークンは返さない |
| `Profile` | `id`, `display_name`, `created_at`, `web_notifications`, `discord_dm_notifications` | `onboarded_at` |
| `AgentSettings` | `autonomy_enabled`, `frequency_per_day`, `daily_budget_usd`, `max_session_usd`, `web_visibility`, `discord_publish`, `updated_at` | なし |
| `Relationship` | `other_agent_id`, `other_agent_name`, `conversation_count`, `shared_interests`, `last_met_at`, `blocked` | なし |
| `Guild` | `guild_id`, `channel_id`, `guild_enabled`, `consented_at` | `revoked_at` |
| `Preferences` | `web_notifications`, `discord_dm_notifications` | DMは初期OFF |
| `DeletionRequest` | `id`, `status`, `requested_at` | `completed_at` |

## エンドポイント一覧

| Method / Path | 目的・認可 | 要求 → 応答 | 主エラー | DB | 画面 |
|---|---|---|---|---|---|
| `GET /users/me` | 招待・有効状態を確認して自分を作成/取得（operationId: `get_current_user`） | — → `Profile` | 401,403,503 | profiles | `/onboarding`, `/settings/account` |
| `PATCH /users/me` | 本人情報更新（operationId: `patch_current_user`） | `display_name` → `Profile` | 401,403,422,503 | profiles | `/settings/account` |
| `POST /users/me/deletion` | 本人の削除要求 | `confirm:true` → `202 DeletionRequest` | 403 `REAUTHENTICATION_REQUIRED` | deletion_requests, agents | `/settings/account` |
| `GET /users/me/deletion` | 本人の削除状況 | — → `DeletionRequest` | 404 | deletion_requests | `/deletion-status` |
| `GET /agents/me` | 自分のAI取得 | — → `Agent`または404 | 404 | agents, agent_settings | `/dashboard`, `/agent` |
| `POST /agents` | 下書き作成 | `AgentDraftInput` → `201 Agent` | 409,422 | agents, agent_profiles | `/agent/new` |
| `PATCH /agents/{id}` | 名前/アイコン更新 | `name?,icon_path?` → `Agent` | 403,422 | agents | `/agent/edit` |
| `GET /agents/{id}/profile` | 自分の人格取得 | — → `ProfileRevision` | 404 | agent_profiles | `/agent`, `/agent/edit` |
| `GET /agents/{id}/profile/private` | 本人の非公開欄を編集用に復号 | — → `{private_info}` | 404 | agent_profiles | `/agent/edit` |
| `POST /agents/{id}/profile/revisions` | 新しい人格下書き | `AgentDraftInput` → `ProfileRevision` | 409,422 | agent_profiles | `/agent/edit` |
| `POST /agents/{id}/profile/approve` | 版承認・有効化 | `version` → `Agent` | 409 | agent_profiles, agents | `/agent/new`, `/agent/edit` |
| `POST /agents/{id}/profile/suggest` | 任意のLLM下書き提案 | `answers` → `ProfileRevision`（未承認） | 429,503 | agent_profiles, usage_records | `/onboarding` |
| `GET /agents/{id}/settings` | 本人の設定取得（operationId: `get_interaction_settings`） | — → `AgentSettings` | 404 | agent_settings | `/settings/interaction` |
| `PATCH /agents/{id}/settings` | 同意/予算/公開変更（operationId: `patch_interaction_settings`） | `AgentSettingsPatch` → `AgentSettings` | 404,409,422 | agent_settings, audit_events | `/settings/interaction` |
| `PUT /agents/{id}/blocks/{other_id}` | 相手をブロック（operationId: `block_agent`。再送も204） | — → 204 | 404,422 | agent_blocks | `/relationships`, `/conversations` |
| `DELETE /agents/{id}/blocks/{other_id}` | 解除（operationId: `unblock_agent`。未ブロックでも204） | — → 204 | 404,422 | agent_blocks | `/relationships` |
| `GET /conversations` | 参加会話一覧（operationId: `list_conversations`） | `cursor,limit` → paged `ConversationSummary` | 401,422 | conversations, participants | `/conversations` |
| `GET /conversations/{id}` | 参加会話全文（operationId: `get_conversation`） | — → `ConversationDetail` | 404 | conversations, messages | `/conversations/[id]` |
| `GET /memories` | 自分の記憶 | `agent_id,cursor` → paged `Memory` | 404 | memories | `/memories` |
| `DELETE /memories/{id}` | 自分の記憶を削除 | — → 204 | 404 | memories, audit_events | `/memories` |
| `GET /relationships` | 自分の交流相手 | `cursor` → paged `Relationship` | 401 | agent_relationships | `/relationships` |
| `GET /notifications` | 本人通知（operationId: `list_notifications`） | `cursor,limit,unread?` → paged `Notification` | 401,422 | notifications | `/notifications` |
| `PATCH /notifications/{id}/read` | 本人通知既読（operationId: `read_notification`。再送しても最初の既読日時） | — → `Notification` | 404 | notifications | `/notifications` |
| `GET /discord/connection` | 連携状態 | — → `DiscordConnection` | 401 | discord_connections | `/settings/discord` |
| `POST /discord/link/start` | state生成、認可URL返却 | — → `url,state_expires_at` | 409 | discord_oauth_states | `/settings/discord` |
| `POST /discord/link/complete` | OAuth code交換 | `code,state` → `DiscordConnection` | 400,409 | discord_oauth_states, discord_connections | `/auth/discord/callback` |
| `DELETE /discord/connection` | 本人連携解除 | — → 204 | 404 | discord_connections | `/settings/discord` |
| `GET /discord/guilds` | 本人が同意したギルドと設定 | — → `Guild[]` | 401 | discord_guilds, channels, agent_discord_consents | `/settings/discord` |
| `PUT /discord/guilds/{id}/channel` | 管理者が公開先設定 | `channel_id,enabled` → `GuildChannel` | 403,422 | discord_guilds, channels | `/settings/discord` |
| `GET /preferences` | 通知等の個人設定（operationId: `get_preferences`） | — → `Preferences` | 401 | profiles（MVP） | `/settings/account` |
| `PATCH /preferences` | 通知設定変更（operationId: `patch_preferences`） | `web_notifications?,discord_dm_notifications?` → `Preferences` | 422 | profiles（正式版で専用表） | `/settings/account` |

`/discord/link/complete`はNext.jsのServer RouteからBearer付きで呼び、stateの本人IDと一致させる。ギルド別本人同意はMVPではギルド内のBotコマンド`/agent publish`で行い、Webは状態を表示する。`/discord/guilds/{id}/channel`は正式版候補で、Web本人が連携済みかつDiscordのギルド管理権限を持つことを、その場で再認可したOAuthトークン等で検証できるまでは公開しない。MVPではBotの`/server setup`を使う。

## 契約運用

バックエンド担当がPydantic schemaとOpenAPIを先にPRにし、フロント担当は生成クライアントを使う。Mock serverは同じOpenAPIから作る。互換性を壊す変更は`/api/v2`または事前の移行期間を設ける。`request_id`と`Idempotency-Key`は監査ログに記録し、本文・秘密を除外する。認証/認可の実装順は [ロードマップ](18-development-roadmap.md)。

## 開発基盤の運用エンドポイント

上記の業務APIは`GET/PATCH /users/me`、#15の人格/アイコン、#16の交流設定・ブロック・通知設定、#21の会話・通知を実装済み。その他は将来契約。ほかに`GET /health`（`/api/v1`外、認証不要、operationId: `get_health`）を提供する。Pydantic `Health`の応答は`{status:"ok",service:"api",mode:"mock"|"live"}`。これはHTTPプロセスの生存確認であり、DB/Authのreadinessを保証しない。FastAPIから生成したOpenAPIとTypeScript型は`packages/api-client/`に管理する。

## 実装済みの共通基盤（#7 前半）

`apps/api/src/alter_api/`に置き、業務ルーターはこれらを使う。

| 項目 | 実装 | 契約 |
|---|---|---|
| 基点 | `main.create_api_v1_router()` | 業務APIは`/api/v1`配下に追加する。`/health`は対象外 |
| エラー | `errors.py` | 全エラーを`{"error":{code,message,request_id}}`で返す。`code`はOpenAPIの`ErrorBody`のenum。404/405/422/500も同形式。422は入力値を応答に反射しない。500はHTTPExceptionのdetailを含め例外内容を返さない。応答開始前の予期しない例外はrequest_idと型名だけを記録して500に変換し、ASGIサーバーに再送出しない |
| request_id | `errors.RequestIdMiddleware` | リクエストごとにサーバーがUUIDを採番し、`X-Request-ID`応答ヘッダーとエラー本文に入れる。クライアント送信値は採用しない。CORSで`X-Request-ID`を公開する。許可Originからの500応答にもCORSヘッダーを付ける |
| ページング | `pagination.page_params` / `Page[T]` | `limit` 1〜50（既定20）、`cursor`は1〜512字の不透明文字列。応答`{items,next_cursor}` |
| Idempotency-Key | `idempotency.idempotency_key` | 印字可能ASCII 8〜255字（UUID推奨）、形式不正は422。キーと結果の保存・再送時の同一応答は、キーを使う最初の書込APIのIssueで保存先と合わせて実装する |
| CORS | `main.create_app` | `CORS_ORIGINS`の明示Originだけ許可（`*`は設定エラー）。メソッドGET/POST/PATCH/PUT/DELETE、ヘッダー`Authorization`/`Content-Type`/`Idempotency-Key` |
| 認証 | `auth.CurrentPrincipal` | [認証](06-authentication-authorization.md)の手順でJWTを検証し、`Principal.sub`（UUID）を渡す。未認証・不正は401 `UNAUTHENTICATED`（`WWW-Authenticate: Bearer`）、JWKS/Auth照会障害は503 `AUTHORIZATION_UNAVAILABLE` |
| 共通認可 | `business.BusinessConnection` | 一般業務APIはこれで業務用のDB接続（トランザクション）を受け取る。`live`モード以外は503 `SERVICE_UNAVAILABLE`（Mockで認可を迂回しない）。JWTの後、Auth管理APIで本人のidentityを取得し、招待の失効とロック順をそろえるため`invitations`・`profiles`の本人行を`FOR UPDATE`で読む。プロフィール削除済み・招待失効済みは403 `ACCOUNT_INACTIVE`、招待外・未登録/未対応identity・Authユーザー消去済みは403 `INVITATION_REQUIRED`。毎リクエスト確認しキャッシュしない。通過した最初の要求で`profiles`を`INSERT ... ON CONFLICT DO NOTHING`で作成し（初期表示名`Alter user`）、拒否した要求では作成しない。DB・Auth管理API障害、招待データ不整合は503 `AUTHORIZATION_UNAVAILABLE` |
| `/users/me` | `users.py` | `Profile`は`{id,display_name,created_at,web_notifications,discord_dm_notifications,onboarded_at}`（#44 のpreview契約と同じ）。`GET`は共通認可が作成/確認したプロフィールを返す。`PATCH`は`display_name`だけを受け付け（他の項目は422）、前後の空白を除いて1〜80字、空白だけ・制御文字は422。削除状態のプロフィールを更新で復活させない。応答は`Cache-Control: no-store`。OpenAPIには共通エラー401/403/404/409/422/429/503を載せる |

JWT検証の設定は`SUPABASE_URL`・`SUPABASE_JWT_ISSUER`・`SUPABASE_JWT_AUDIENCE`・`SUPABASE_JWT_ALGORITHMS`（ES256/RS256のみ、既定両方）・`SUPABASE_JWKS_CACHE_SECONDS`（既定300）。JWKSは`kid`ごとにキャッシュし、未知の`kid`では一度だけ再取得する（再取得は10秒以上の間隔）。ヘッダーの`alg`は許可リストかつ鍵の種類と一致する場合だけ受け付ける。時刻の許容差は30秒。JWKSが空のプロジェクトの`HS256`トークンだけを、`exp`/`iss`/`aud`/`sub`/`role`の確認後に`GET /auth/v1/user`（`apikey`は`SUPABASE_PUBLISHABLE_KEY`）で照会する。JWKSに鍵がある状態の`HS256`は拒否する。JWKS内の鍵が不正・未対応の場合は503とし、空JWKSと扱ってHS256へ切り替えない。トークン・鍵・Publishable Keyはログやエラーに出さない。`ALTER_MODE=mock`でも検証は省略しない。

### 会話費用の表示対象（#21 / PR #47レビュー）

会話一覧・詳細の`cost_usd`は本人のAIがこの会話で消費した確定費用（USD）だけを返す。
2体分の合計や相手の利用額を返さない。UIは「自分のAIの確定費用」と表示し、nullを0に補完しない。
`usage_records.kind=charge`の`actual_usd`を合計する。queued/runningはnull、終了後の課金なしは0（下記#21）。
#23の4画面は正式生成クライアントを利用し、previewの会話・通知も正式routerを再利用する。

Issue #8のMockは同じuserルーターとモデルを再利用する。`/__mock/profiles`はページングを試す合成データ専用契約で、本番APIに追加しない。手順は[フロント開発](24-frontend-development.md)を参照。

## #16 交流設定・ブロック・通知設定の実装

- `PATCH /agents/{id}/settings`は1項目以上・null不可・未知項目は422（#48 のpreview契約と同じ）。真偽値・整数・金額は型を厳密に検査し、`"yes"`や`"0.1"`のような文字列は422。
- 予算はUSDで0以上・小数6桁まで。`daily_budget_usd`は運営設定`AGENT_DAILY_BUDGET_MAX_USD`（APIの環境変数、既定1.00）以下、`max_session_usd`は保存後の`daily_budget_usd`以下でなければ422。この上限の確認は、予算項目を変更するときと同意をONにするときだけ行い、運営が上限を下げたあとでもOFF（同意の撤回）や頻度の変更は既存予算の修正なしに受け付ける。金額は1,000,000 USDを超える値も422（500にしない）。日次予算を下げるときは`max_session_usd`も同時に下げる。
- `autonomy_enabled`と`discord_publish`をONにできるのは、承認済みで`status='active'`のAIだけ（下書き・停止中は409）。自律交流のONは同意文面の版`ALTER_CONSENT_VERSION`（APIの環境変数）が設定されているときだけで、未設定なら409（送信先LLM未決定の間はONにさせない）。Discordのギルド別同意・管理者設定は配信時に別途確認する（#26/#28）。
- 自律交流をOFFにすると、そのAIが参加する`queued`の会話とジョブ（`queued`/`retry_wait`）を同じトランザクションで`cancelled`にする。会話が`queued`でも、Workerがジョブをすでに取得済み（`running`）なら取り消さない（会話とジョブを行ロックしてから判断する）。取り消した会話の未使用予約の解放は、#59 の共通解放処理を使う別PRで接続する（予約を作る #60 より前に入れる）。実行中の会話はWorkerが発言前の再確認で止める。取り消した会話の予約解放は終了処理（#19/#20）で行う。`discord_publish`のOFFで止める未配信は配信表を追加する #28 で接続する。
- ブロックは本人のAIから相手のAIへの片側の記録。ブロック・解除の最初に自分と相手のAIをIDの昇順で行ロックし、相互ブロックが同時に来てもデッドロックしない。自分自身は422、存在しない・削除済みの相手は404。新しくブロックしたときは、その相手との`queued`の会話とジョブを取り消す。解除はブロックしていなくても204。
- 変更があったときだけ`audit_events`へ記録する（`agent_settings.update`/`agent.block`/`agent.unblock`/`preferences.update`）。メタデータは変更した項目名、同意系のON/OFF、自律交流ON時の同意文面の版、取り消した会話の件数だけで、ブロックした相手のIDは入れない。
- 他人のAIの`{id}`は存在を隠して404。招待・停止/削除の確認は共通認可（`BusinessConnection`）で行う。

## #15 人格APIの実装

`GET /agents/me`、作成/名前更新、人格取得/新規版/承認/privateを実装。
`RevisionInput.expected_version`は必須（最新下書き版との不一致は409）。承認は最新の版だけを許可し、
同じ最新版が承認済みなら追加更新せず現在の`Agent`を200で返す。新版作成後の旧版承認は409。
承認APIは`Idempotency-Key`を契約に含めず、キー別の過去応答は保存しない。
初回承認成功時に、同じDBトランザクションで`profiles.onboarded_at`をサーバー時刻で設定する。
作成・下書き保存ではNULL、再送・再承認では初回日時を維持し、承認失敗では記録しない。
承認では交流・Discord公開をONにしない。
`GET /agents/{id}/profile`は版指定なしなら最新の版（承認状態を問わない）を返す。
任意の`?version=N`（整数、1以上）で特定版を取得でき、版不存在・他人所有は404、指定不正は422。
どの版でも非公開欄は含めない。Workerは承認版を使用する。
作成の`Idempotency-Key`は本人・操作ごとに分離し、`api_idempotency`へ201の成功応答と入力HMAC・鍵版を保存する。
成功から24時間以内の同一キー・同一入力は保存済み応答を201で返し、本文不一致は409。
再送しても期限は延長しない。期限後は保存済み応答を返さず、新規作成として検証するため、
既存AIがあれば409（1人1体の制約は維持）。清掃の実行有無でこの挙動は変わらない。
HMACは暗号化鍵から独立した32byte鍵を使う。`API_IDEMPOTENCY_HMAC_KEYS`は
版番号→Base64鍵のJSON、`API_IDEMPOTENCY_HMAC_ACTIVE_VERSION`（既定1）は新規記録の鍵版。
再送は記録の鍵版で照合するため、暗号化鍵の交換やHMACの現行版切替で過去の再送を壊さない。
照合後の再送には暗号化鍵を要求しない。必要なHMAC鍵の未設定・不正・旧版欠落は503で、書込は行わない。
ヘッダーなしの作成ではHMAC鍵を要求しない。
交換時は新旧のHMAC鍵を全APIインスタンスへ配布してから現行版を切替え、旧版の最終作成成功から24時間経過後に旧鍵を外す。
同じ鍵版の値は上書きしない。再送保証・保持契約は[DB設計](05-database-design.md)を参照。
privateはAES-256-GCM（32byte鍵、ランダムnonce、agent/versionをAAD）で保存、応答はno-store。
鍵不備は503。人格暗号化は現在V1鍵のみで、鍵交換CLI/復元訓練は#36の運用受け入れで確認する。
業務APIはliveでのみDBを利用し、毎リクエストのAuth管理照会と招待/プロフィールロックを通す。
Mockでも認可は迂回せず503。`GET/PATCH /users/me`（#66）および交流/通知設定（#69）のルーターも同時に登録する。

## #15 アイコンのAPIと所有権（2026-11-14）

`POST /api/v1/agent-icons`（operationId: `upload_agent_icon`）は本人の招待/停止状態を検証し、
Content-Type `image/png` / `image/jpeg` / `image/webp`の**画像バイナリ本文**を受け付ける。
multipartや任意URLは受け付けない。入力/再エンコード後とも最大2 MiB、縦横各2048pxまで。
MIMEと実形式の一致・完全なデコード・単一フレームを検査し、PNGへ再エンコードして
EXIF/GPS/コメント/末尾の異種データを除去する。SVG/GIF/破損/サイズ超過は422。
成功時は201 `{icon_path: "<本人UUID>/<サーバー採番UUID>.png"}`。
他人のprefix・未登録パス・private用途bucket・外部URLを`icon_path`へ指定すると422。
認証/招待拒否時は画像を保存しない。Storage資格情報不備/障害/timeoutは秘密なしの503。

アップロードは専用private bucket `agent-icons`へimmutableに書き込み、API専用
`agent_icon_uploads`に本人・検証済みMIME/サイズ/hashを記録する。ブラウザは直接CRUDできない。
私的ファイル用の`profile-private` bucketも非公開で、アイコンAPIはこのbucketを署名しない。
アイコンの公開用途には**300秒の署名URL**を使い、無期限のpublic URLは発行しない。
選択済みの検証済みアイコンだけが`Agent.icon_url`に入り、生パス/私的ファイルURLは返さない。

`POST /agents`の`icon_path?`でアップロード済み画像を選べる。
`PATCH /agents/{id}`は`name?`/`icon_path?`の1項目以上を受け付け、name:nullは422。
icon_pathの省略は維持、nullは解除、非nullは検証済みの本人画像への変更。
他人所有のAIはStorage検証前に404。人格revisionではアイコンを変更せずPATCHを使う。
既存のアイコンは本人のGET/更新/承認応答で署名URLを再発行する。
作成Idempotency-Keyの保存応答には署名tokenを保存せず、同じ入力の再送時は
元のアイコンの短期URLを更新する（id/人格等の作成結果は維持）。

解除/招待失効後の新規署名は拒否するが、発行済みURLは最大300秒残る。
画像UI/ファイル選択は#17側の画面実装、APIの画像検証は本Issueの範囲。

## #21 会話一覧・詳細とWeb通知の実装

- 一覧・詳細は本人のAIが参加している会話だけ。参加していない会話の`{id}`は存在を隠して404。並びは作成日時の新しい順（同時刻はID順）で、cursorは不透明な文字列（不正は422）。
- 参加者の利用者が削除要求済み（`profiles.deleted_at`）またはAIが削除済みの会話は、要求時点から双方の一覧・詳細・通知から外す（#30 の削除要求は同じトランザクションで`profiles.deleted_at`を記録するため、この条件で要求時点から外れる）。
- `messages`は`moderation_status='allowed'`の発言だけを`turn_no`順に返し、モデレーション状態・トークン数は返さない。`participants`は参加順、`partner`は自分以外の参加者のAI。
- `cost_usd`（#47 で合意）: 本人のAI・この会話・`usage_records.kind='charge'`の`actual_usd`の合計。予約（reserve）・解放（release）と相手のAIの費用は含めない。会話が`queued`/`running`の間は確定前として`null`、`completed`/`failed`/`cancelled`では合計（課金なしは0）。`relationship_change`は現時点では常に`null`。
- `Notification.body`は表示用の文字列で、APIが`kind`から組み立てる。DBの`notifications.body`（JSON）や会話本文・相手の非公開情報は返さない。現在の`kind`は`conversation.completed`（「〇〇との交流が完了しました。」）。未知の種類は汎用文言にする。
- `PATCH /notifications/{id}/read`は`read_at`を`coalesce(read_at, now())`で更新し、再送しても最初の既読日時を保つ。他人の通知は404。
- 完了トランザクション用に`alter_domain.activity.notify_conversation_completed(conn, conversation_id)`を用意した。Workerが会話を`completed`にするのと同じトランザクションで呼ぶ（#20）。`completed`の会話だけが対象で、`dedupe_key='conversation.completed:<会話ID>'`により再実行しても1件。Web通知OFF・削除要求済みの利用者・削除済みAIには作らず、`body`は空のJSONにして会話本文を複製しない。
- 会話・通知は作成から90日（docs/15・docs/23の保持期間）を過ぎた時点で一覧から外し、ID指定・既読は404にする。行の削除は日次パージ（#30）が行い、それまでもAPIからは見せない。

## #30 前半 アカウント削除の受付と即時停止

`apps/api/src/alter_api/deletion_routes.py`と`backend/alter_domain/src/alter_domain/account_deletion.py`で実装した。operationIdは #51 のpreview契約から`preview_`を外した`request_deletion`・`get_own_deletion`。応答は`Cache-Control: no-store`。

- 再認証: `POST`はJWTの`amr`に5分以内（時計のずれ30秒まで）の`oauth`が必要。なければ403`REAUTHENTICATION_REQUIRED`で何も変えない。トークンの更新では`amr`の時刻は変わらないため、古いログインは通らない（#51 の再認証画面と同じ5分）。
- 本文は`{confirm:true}`だけ（ほかの値・未知項目は422）。`Idempotency-Key`は任意で受け付けるが、削除要求は利用者ごとに1件のため、キーがなくても重複しない。受付済みの再送は再認証なしで同じ要求を202で返す。
- 招待外は403`INVITATION_REQUIRED`、運営が失効済みは403`ACCOUNT_INACTIVE`（本人からは削除要求できない。運営が対応）。
- 受付は1トランザクション: `profiles.deleted_at`の記録、AIの自律交流OFFと`paused`、未開始（queued）の交流とジョブの取消（Worker取得済みは取り消さない）、未配信の取消、招待の失効、`deletion_requests`（pending）の作成、非同期パージ用ジョブ`account.purge`（`dedupe_key='account.purge:<要求ID>'`）の投入、監査（要求IDと停止・取消件数だけ。表示名などは残さない）。途中で失敗したら全部戻して503。
- 以後の業務APIは403`ACCOUNT_INACTIVE`。共有会話・通知は`profiles.deleted_at`により相手の画面からも外れる（#21）。
- `GET`は本人JWTだけで確認できる（削除要求後は共通認可が403のため、招待の確認をしない）。本人の最新の要求のID・状態・日時だけを返し、なければ404。
- `deletion_requests`は Auth 削除後も完了を追えるよう`profiles`へのFKを持たない。進行中（pending/running）は利用者ごとに1件、`error_code`は短い固定コードだけ。
- 実際の削除（会話→記憶→通知→AI→プロフィール→Auth）、Discord投稿の削除試行、30日以内のパージ・匿名化は #30 後半（#22・#28 の後）で`account.purge`ジョブとして実装する。



## #24 Web設定画面の接続

Webの交流設定・ブロックは正式OpenAPI生成クライアントを利用する。
previewも正式settings routerを再利用し、Patchの厳密型・null/未知項目拒否・認証定義を共有する。
ブロック/解除の204をJSONとして読まず、BFFも空本文を返す。APIエラーのrequest_idとRetry-Afterを保持する。
画面は確認Dialogの成功応答後だけ設定を更新し、ブロック後は会話を再取得する。
