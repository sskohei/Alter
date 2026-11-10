# 認証・認可

## Webログインとセッション

Google/GitHubをSupabase AuthのOAuthプロバイダーとして有効化する。Next.js App Routerは`@supabase/ssr`のブラウザ/サーバークライアントを分け、PKCEコールバックでセッションCookieを設定する。認証済みルートは動的レンダリングにし、トークン更新を行うProxy（Next.js 16以降）またはMiddleware（15以前）を配置する。更新Cookieを含むレスポンスを共有キャッシュに保存しない。ログアウトはSupabaseセッションを破棄し、フロントのQuery cacheも消す。[Supabase SSRクライアント](https://supabase.com/docs/guides/auth/server-side/creating-a-client)、[SSRのキャッシュ注意](https://supabase.com/docs/guides/auth/server-side/advanced-guide)（確認日2026-10-27）。

FastAPIへのHTTPSリクエストは`Authorization: Bearer <Supabase access_token>`。Next.jsのサーバー経由でAPIへ送る場合も同じトークンを用い、refresh tokenは送らない。401時は一度だけSupabaseで更新して再送し、なお401ならログインへ。CORSは本番Web URLとPreviewの登録済みOriginだけを許可し、`Authorization`ヘッダーを許す。ワイルドカードOriginを使わない。

## FastAPIでのJWT検証

1. `Authorization`のBearer形式と長さを確認。許可する`alg`を設定で固定し、ヘッダー値で動的に選ばない。
2. 非対称署名キーのSupabaseプロジェクトでは`https://<project>.supabase.co/auth/v1/.well-known/jwks.json`からJWKSを取得し、`kid`一致・署名検証。キーを短時間キャッシュし、未知の`kid`で一度だけ更新する。
3. `iss == https://<project>.supabase.co/auth/v1`、`aud == authenticated`、`exp`・`nbf`、`sub`がUUIDであること、`role == authenticated`を検査する。時刻の許容差は30秒以下。
4. 共有鍵HS256運用の場合はJWKSが空になり得る。Authサーバー`GET /auth/v1/user`でトークンを検証する方式に切り替え、ローカルでJWT secretを配布しない。運用では非対称鍵に移行してから公開する。
5. JWT検証後、既存の`profiles.deleted_at`・停止状態と進行中の削除要求を優先確認し、[招待契約](23-invitation-consent-operations.md)に従ってGoogle/GitHubの信頼できるprovider ID・`sub`とDBの`invitations`・`invitation_identities`の有効な招待を毎リクエスト照合する。許可結果をリクエスト間でキャッシュしない。登録・失効は運営専用CLIから共通ドメイン処理を通す（ADR-014、#7）。メール/表示名/編集可能metadataを使用しない。招待外は403 `INVITATION_REQUIRED`、停止/削除要求済みは403 `ACCOUNT_INACTIVE`、招待判定障害は503 `AUTHORIZATION_UNAVAILABLE`として業務処理を止める。
6. 招待判定通過後だけ`sub`を`profiles.id`として取得/初期化する。失効直後など短いJWT有効期間内のリスクには重要操作でAuth側確認を追加する。

署名方式とJWKS挙動は[Supabase公式JWT資料](https://supabase.com/docs/guides/auth/jwts)（2026-10-27確認）。未認証・無効JWTは401、招待/停止/削除による拒否は上記403、所有権違反は存在を隠す404または403、入力不正は422。APIログにJWTを出さない。

## リソース認可表

| 操作 | 条件 |
|---|---|
| `agents`・設定・人格更新 | `agents.owner_id == token.sub` |
| 会話/発言参照 | `conversation_participants.agent_id`から本人所有を確認。MVPは参加者のみ |
| 記憶参照/削除 | 記憶の`agent_id`が本人所有 |
| 通知参照/既読 | `notifications.profile_id == token.sub` |
| ブロック | blockerが本人所有。blockedは存在する公開可能AIのみ |
| Discord本人連携解除 | `discord_connections.profile_id == token.sub` |
| Discord `/agent pause`等 | Discord IDから有効連携を解決し、同じ所有権条件を適用 |
| Discord `/server setup` | 呼出者のギルド管理権限とBotの必要権限をDiscord APIで確認 |

クライアントの`owner_id`は採用せず、必ずトークンから埋める。管理DB接続によるRLS迂回は [DB設計](05-database-design.md) の通りで、各SQLに本人条件を含める。権限判定は一覧取得にも適用する。

## Discord連携はログインとは別

Supabaseログイン後のWeb画面からDiscord OAuth2 authorization codeフローを開始し、短命・使い捨て`state`のハッシュを`discord_oauth_states`に本人IDと保存する。Discordのリダイレクト先はNext.jsの`/auth/discord/callback`。このServer Routeが現在のSupabaseセッションを確認し、`code,state`をBearer付きでFastAPIの`POST /discord/link/complete`へ転送する。APIはstateの本人ID・期限・未使用を照合してからDiscordでcodeを交換し、`identify`のユーザーIDを取得する。既存の有効連携と衝突すれば409で止める。Discord OAuthをSupabaseのログインプロバイダーとしては用いない。MVPでは永続的なDiscordユーザーアクセストークンは保存せず、`identify`取得後に破棄する。解除は紐付けを失効し、未配信通知も停止する。Bot導入は別の`bot`/`applications.commands`フローで、ギルド管理者が実行する。[Discord OAuth2](https://docs.discord.com/developers/topics/oauth2)。

## アカウント削除

本人再認証後に削除を要求し、即時にAI停止・公開配信取消・招待失効・監査・`deletion_requests`作成を同一DBトランザクションで確定し、非同期削除を追跡する。会話を共有する相手の画面からも対象会話を削除する方針をMVPに採用し、事前に画面で説明する。要求時点で関連会話を双方から参照不可とし、派生記憶/通知も削除する。許可リストを失効させ、DB/Storageパージ後にSupabase Admin APIでAuthユーザーを削除する。30日以内の完了をsskoheiが追跡し、再OAuthで自動復帰させない。状況確認だけの本人認証の例外とAuth削除後の窓口は[運用契約](23-invitation-consent-operations.md)に従う。Discord投稿の削除は権限がある範囲で試行し、実行結果を記録する。詳細は[セキュリティ](15-security-privacy.md)。
