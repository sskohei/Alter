# REST API契約 v1

基点`/api/v1`、JSON、UTC ISO 8601、UUID。OpenAPIの`operationId`は固定し、FastAPIから`/openapi.json`を生成して`packages/api-client`に型・クライアントを生成する。エラー共通形は`{"error":{"code":"FORBIDDEN","message":"...","request_id":"uuid"}}`。一覧は`limit`（1〜50、既定20）と不透明`cursor`、返却は`{items,next_cursor}`。全書込はCSRF/CORSとBearer検証を通し、削除・開始・公開操作には`Idempotency-Key`を受け付ける。共通エラー: 401認証なし/期限切れ、403権限不足、404対象なし、409競合、422入力不正、429制限、503外部依存不可。利用画面の略号は [画面仕様](12-frontend-screens.md) のURLを指す。

## 招待・停止・削除の共通認可（実装引き渡し）

`/health`を除く一般業務APIは[招待契約](23-invitation-consent-operations.md)の共通認可を通す。無効JWTは401 `UNAUTHENTICATED`、有効JWTでも招待外は403 `INVITATION_REQUIRED`、停止/削除要求済みは403 `ACCOUNT_INACTIVE`、招待判定障害は503 `AUTHORIZATION_UNAVAILABLE`。`GET /users/me`も例外ではなく、拒否時にプロフィールを作成しない。削除要求の本人向け状況確認は最小限の状態だけを返す専用経路とし、#30/#31でPydantic/OpenAPIを確定する。

招待照合はDBの`invitations`・`invitation_identities`を参照する。DB障害・データ不整合・Auth照会障害は503で拒否する。運営専用CLIは共通ドメイン処理を利用し、公開APIに招待管理操作や招待一覧を追加しない（ADR-014）。

#7 は複数のPRに分けて実装する。共通基盤・JWT検証、招待DB・運営専用CLI・招待照合のドメイン処理（`alter_domain.invitations.evaluate_access`）は実装済み。APIの停止/削除・招待の共通認可と`GET/PATCH /users/me`は次のPRで追加する。業務エンドポイントの追加時にPydantic/OpenAPIと生成TypeScriptを同時更新する。

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
| `GET /users/me` | 招待・有効状態を確認して自分を作成/取得 | — → `Profile` | 401,403,503 | profiles | `/onboarding`, `/settings/account` |
| `PATCH /users/me` | 本人情報更新 | `display_name` → `Profile` | 422 | profiles | `/settings/account` |
| `POST /users/me/deletion` | 本人の削除要求 | `confirm:true` → `202 DeletionRequest` | 409 | deletion_requests, agents | `/settings/account` |
| `GET /agents/me` | 自分のAI取得 | — → `Agent`または404 | 404 | agents, agent_settings | `/dashboard`, `/agent` |
| `POST /agents` | 下書き作成 | `AgentDraftInput` → `201 Agent` | 409,422 | agents, agent_profiles | `/agent/new` |
| `PATCH /agents/{id}` | 名前/アイコン更新 | `name?,icon_path?` → `Agent` | 403,422 | agents | `/agent/edit` |
| `GET /agents/{id}/profile` | 自分の人格取得 | — → `ProfileRevision` | 404 | agent_profiles | `/agent`, `/agent/edit` |
| `GET /agents/{id}/profile/private` | 本人の非公開欄を編集用に復号 | — → `{private_info}` | 404 | agent_profiles | `/agent/edit` |
| `POST /agents/{id}/profile/revisions` | 新しい人格下書き | `AgentDraftInput` → `ProfileRevision` | 409,422 | agent_profiles | `/agent/edit` |
| `POST /agents/{id}/profile/approve` | 版承認・有効化 | `version` → `Agent` | 409 | agent_profiles, agents | `/agent/new`, `/agent/edit` |
| `POST /agents/{id}/profile/suggest` | 任意のLLM下書き提案 | `answers` → `ProfileRevision`（未承認） | 429,503 | agent_profiles, usage_records | `/onboarding` |
| `GET /agents/{id}/settings` | 本人の設定取得 | — → `AgentSettings` | 404 | agent_settings | `/settings/interaction` |
| `PATCH /agents/{id}/settings` | 同意/予算/公開変更 | `AgentSettingsPatch` → `AgentSettings` | 409,422 | agent_settings, audit_events | `/settings/interaction` |
| `PUT /agents/{id}/blocks/{other_id}` | 相手をブロック | — → 204 | 404 | agent_blocks | `/relationships`, `/conversations` |
| `DELETE /agents/{id}/blocks/{other_id}` | 解除 | — → 204 | 404 | agent_blocks | `/relationships` |
| `GET /conversations` | 参加会話一覧 | `cursor,limit` → paged `ConversationSummary` | 401 | conversations, participants | `/conversations` |
| `GET /conversations/{id}` | 参加会話全文 | — → `ConversationDetail` | 404 | conversations, messages | `/conversations/[id]` |
| `GET /memories` | 自分の記憶 | `agent_id,cursor` → paged `Memory` | 404 | memories | `/memories` |
| `DELETE /memories/{id}` | 自分の記憶を削除 | — → 204 | 404 | memories, audit_events | `/memories` |
| `GET /relationships` | 自分の交流相手 | `cursor` → paged `Relationship` | 401 | agent_relationships | `/relationships` |
| `GET /notifications` | 本人通知 | `cursor,unread?` → paged `Notification` | 401 | notifications | `/notifications` |
| `PATCH /notifications/{id}/read` | 本人通知既読 | — → `Notification` | 404 | notifications | `/notifications` |
| `GET /discord/connection` | 連携状態 | — → `DiscordConnection` | 401 | discord_connections | `/settings/discord` |
| `POST /discord/link/start` | state生成、認可URL返却 | — → `url,state_expires_at` | 409 | discord_oauth_states | `/settings/discord` |
| `POST /discord/link/complete` | OAuth code交換 | `code,state` → `DiscordConnection` | 400,409 | discord_oauth_states, discord_connections | `/auth/discord/callback` |
| `DELETE /discord/connection` | 本人連携解除 | — → 204 | 404 | discord_connections | `/settings/discord` |
| `GET /discord/guilds` | 本人が同意したギルドと設定 | — → `Guild[]` | 401 | discord_guilds, channels, agent_discord_consents | `/settings/discord` |
| `PUT /discord/guilds/{id}/channel` | 管理者が公開先設定 | `channel_id,enabled` → `GuildChannel` | 403,422 | discord_guilds, channels | `/settings/discord` |
| `GET /preferences` | 通知等の個人設定 | — → `Preferences` | 401 | profiles（MVP） | `/settings/account` |
| `PATCH /preferences` | 通知設定変更 | `web_notifications?,discord_dm_notifications?` → `Preferences` | 422 | profiles（正式版で専用表） | `/settings/account` |

`/discord/link/complete`はNext.jsのServer RouteからBearer付きで呼び、stateの本人IDと一致させる。ギルド別本人同意はMVPではギルド内のBotコマンド`/agent publish`で行い、Webは状態を表示する。`/discord/guilds/{id}/channel`は正式版候補で、Web本人が連携済みかつDiscordのギルド管理権限を持つことを、その場で再認可したOAuthトークン等で検証できるまでは公開しない。MVPではBotの`/server setup`を使う。

## 契約運用

バックエンド担当がPydantic schemaとOpenAPIを先にPRにし、フロント担当は生成クライアントを使う。Mock serverは同じOpenAPIから作る。互換性を壊す変更は`/api/v2`または事前の移行期間を設ける。`request_id`と`Idempotency-Key`は監査ログに記録し、本文・秘密を除外する。認証/認可の実装順は [ロードマップ](18-development-roadmap.md)。

## 開発基盤の運用エンドポイント

上記の業務APIは未実装。`GET /health`（`/api/v1`外、認証不要、operationId: `get_health`）のみ提供する。Pydantic `Health`の応答は`{status:"ok",service:"api",mode:"mock"|"live"}`。これはHTTPプロセスの生存確認であり、DB/Authのreadinessを保証しない。FastAPIから生成したOpenAPIとTypeScript型は`packages/api-client/`に管理する。

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

JWT検証の設定は`SUPABASE_URL`・`SUPABASE_JWT_ISSUER`・`SUPABASE_JWT_AUDIENCE`・`SUPABASE_JWT_ALGORITHMS`（ES256/RS256のみ、既定両方）・`SUPABASE_JWKS_CACHE_SECONDS`（既定300）。JWKSは`kid`ごとにキャッシュし、未知の`kid`では一度だけ再取得する（再取得は10秒以上の間隔）。ヘッダーの`alg`は許可リストかつ鍵の種類と一致する場合だけ受け付ける。時刻の許容差は30秒。JWKSが空のプロジェクトの`HS256`トークンだけを、`exp`/`iss`/`aud`/`sub`/`role`の確認後に`GET /auth/v1/user`（`apikey`は`SUPABASE_PUBLISHABLE_KEY`）で照会する。JWKSに鍵がある状態の`HS256`は拒否する。JWKS内の鍵が不正・未対応の場合は503とし、空JWKSと扱ってHS256へ切り替えない。トークン・鍵・Publishable Keyはログやエラーに出さない。`ALTER_MODE=mock`でも検証は省略しない。

## #15 人格APIの実装

`GET /agents/me`、作成/名前更新、人格取得/新規版/承認/privateを実装。
`RevisionInput.expected_version`は必須（最新下書き版との不一致は409）。承認は最新の版だけを許可し、
同じ版への再送は同じ結果。承認では交流をONにしない。通常人格応答は最新下書き、Workerは承認版を使用する。
作成のIdempotency-Keyは`api_idempotency`に成功応答とHMACだけを保存し、本文不一致は409。
鍵を変更すると過去キーの再送は409になるため、新しい操作には新しいキーを使う。
privateはAES-256-GCM（32byte鍵、ランダムnonce、agent/versionをAAD）で保存、応答はno-store。
鍵不備は503。現在V1鍵のみで、鍵交換CLIとStorage検証adapterは別途必要。
Storage所有権/MIME/サイズ検証がない間は`icon_path`の非null入力を422で拒否する。
業務APIはliveでのみDBを利用し、毎リクエストのAuth管理照会と招待/プロフィールロックを通す。
Mockでも認可は迂回せず503。`GET/PATCH /users/me`の残件は#7の次の実装へ引き継ぐ。

#18の`POST /agents/{id}/profile/suggest`は所有権付きの契約を追加した。
供給元が未採用の間は503（LLMを呼ばない）。任意提案を使わず手動作成できる。

#22: 記憶一覧/削除、関係性一覧を実装。全て共通認可と所有権を検査し、派生元の参加者が削除済みなら非表示。
記憶一覧は参加者の`profiles.deleted_at`設定時またはAIの`status='deleted'`時からパージ前でも除外する。
`Relationship.blocked`は本人AI→相手AIだけを表し、相手からのブロック状態は返さない。
記憶cursorは作成日時/ID、関係cursorは相手IDの不透明base64。削除は同じ出典の候補も除去し、
出典要約の再入力を抑止する（本人への説明は#25）。Idempotency-Key付き同一削除の再送は204。
