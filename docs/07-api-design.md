# REST API契約 v1

基点`/api/v1`、JSON、UTC ISO 8601、UUID。OpenAPIの`operationId`は固定し、FastAPIから`/openapi.json`を生成して`packages/api-client`に型・クライアントを生成する。エラー共通形は`{"error":{"code":"FORBIDDEN","message":"...","request_id":"uuid"}}`。一覧は`limit`（1〜50、既定20）と不透明`cursor`、返却は`{items,next_cursor}`。全書込はCSRF/CORSとBearer検証を通し、削除・開始・公開操作には`Idempotency-Key`を受け付ける。共通エラー: 401認証なし/期限切れ、403権限不足、404対象なし、409競合、422入力不正、429制限、503外部依存不可。利用画面の略号は [画面仕様](12-frontend-screens.md) のURLを指す。

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
| `GET /users/me` | 自分を作成/取得 | — → `Profile` | 401 | profiles | `/onboarding`, `/settings/account` |
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
