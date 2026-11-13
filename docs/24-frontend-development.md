# フロント先行実装とMock

#8は#3（完了）/#7（契約レビュー待ち）に依存する。
実APIの`openapi.json`は`/health`のみ。文書から起こしたレビュー用Pydantic契約は
`apps/api/src/alter_api/contracts/frontend_preview.py`を正本とし、`pnpm api:generate`で
`preview.openapi.json`/`preview-schema.d.ts`を生成する。本番APIへはマウントしない。
CIは両方のOpenAPI/型の未生成差分を拒否する。

`pnpm dev:mock`で127.0.0.1:8001に合成データ専用サーバーを起動する。
`MOCK_SCENARIO=401|403|inactive|404|409|422|429|503|normal|empty`で切り替える。
Bearerは合成文字列でよく、認証の安全性を検証するサーバーではない。本番へ配備しない。
ページングヘルパーは不透明cursorと1〜50件を扱う。業務一覧の空fixtureは対応画面の契約追加時に接続する。

`@alter/api-client/preview`は型付きクライアント、共通エラー（status/code/request_id）、
Bearer転送、応答のリクエストID、一度だけの401更新・再送を提供する。
HTTPエラーの本文をログへ出さない。サーバーではユーザーごと・リクエストごとに生成する。
ブラウザへrefresh tokenを返す独自APIや、Supabase重要テーブルの直接DMLは追加しない。

## mainの実装との統合（2026-11-10）

#43/#55でJWT検証・共通エラー・招待DB/CLIがmainへ入った。previewのエラーは
`alter_api.errors.ErrorResponse`を直接使用し、エラーコードとrequest_idの型を二重管理しない。
Mockエラーも実APIの生成OpenAPIで検査する。request_idはAPIが採番するため、
クライアントはX-Request-IDを送らず応答から取得する（APIのCORS許可ヘッダーに含まれない）。

`GET/PATCH /users/me`を含む業務ルーターは未実装で、招待判定はドメイン処理まで。
プロフィール・人格・会話等のMockを実APIへ切り替える条件はまだ満たさない。
外部OAuth/LLM/Discordを使う自動テストのMockは引き続き維持する。

## #9 SSR認証

依存は#2（完了）、#7（契約待ち）、#8（先行PR）、#5（トークン未提供）。
`@supabase/ssr`のbrowser/server clientとNext 16 ProxyでセッションCookieを更新し、
保護レイアウトとcallbackは`getUser`でAuthサーバーを確認してからAPI `/users/me`で入場判定する。
403招待外/停止、503判定障害は拒否し、OAuth成功だけで入場しない。
Callbackで`onboarded_at`がnullならonboarding、それ以外は許可した内部URLへ遷移する。
API401では1回だけ更新・再送する。画面からのAPIは同一Originの許可リスト付きBFFを通す。
全認証応答はprivate/no-store。ログアウトはSupabase signOut後に全画面遷移し、
永続Query cache/業務データのlocalStorageは使わない。

`apps/web/.env.example`に公開設定、任意のサーバー側`API_URL`とcallback Originを記載した。
実OAuthはSupabase/Google/GitHubの登録済みURLが必要（#33/#35）。
`pnpm test:e2e`は独立したローカルAuth/API合成サーバーを起動する。
アプリにテスト専用認証ルートやCookieの検証を省略する分岐はない。
法務確認済みの正式文面は未提供のため、/legalは開発用案と明示する。

Draftは依存差分をレビューするため #8 → #9 → #17 のbaseを積む。
先行PRのSquash後は残りへmainを取り込み、baseをmainに変更して再検証する。
mainへ直接push/自動mergeは行わない。依存ブランチ向けPRもCIの対象に含める。

## PRレビュー反映（#45）

OAuth callbackの入場判定が403 `INVITATION_REQUIRED` / `ACCOUNT_INACTIVE`なら、
このブラウザのSupabaseセッションを終了して元の拒否理由を表示する。503ではセッションを終了せず再試行できる。
BFF/transportの共通エラーがない場合のコードは`SERVICE_UNAVAILABLE`に統一する。

`SERVICE_UNAVAILABLE`は「サービスを一時的に利用できません。時間をおいて再試行してください。」と表示する。
認証E2Eの503ケースは実APIと同じコードを返し、PC/mobileで文言とセッションCookieの維持を確認する。

`onboarded_at`は最初の`POST /agents/{id}/profile/approve`成功時にAPIが記録し、再承認では日時を保持する。
PR #58でこの記録処理が反映された後に、初回承認→ログアウト→再ログインで
`/onboarding`へ戻らず`/dashboard`へ遷移することを実APIで受け入れ確認する。
Mockの既存ユーザーでの遷移確認だけでは、この受け入れ条件を完了としない。
