# フロント開発とMock

## Issue #8 の依存と契約

#3（CI）と#7（JWT・共通認可・GET/PATCH `/api/v1/users/me`）はmainへ統合済み。
実APIの正本はFastAPI/Pydanticで、`pnpm api:generate`により次を再生成する。

- `packages/api-client/openapi.json` / `src/schema.d.ts`: `/health`と本人プロフィールの正式契約。
- `packages/api-client/preview.openapi.json` / `src/preview-schema.d.ts`: 同じuserルーター・モデルを再利用し、合成一覧`/__mock/profiles`だけを追加する開発用契約。本番APIにはマウントしない。

`@alter/api-client`の`createApiClient(baseUrl, fetcher?)`に`authenticatedFetch`を渡すと、
Bearer転送、no-store、一度だけの401更新・再送が使える。サーバーではリクエストごとに生成する。
トークン取得がnullなら既存Authorizationを除去する。403/503ではトークンを更新しない。
`unwrap`は生成された共通エラー型、HTTP status、API採番のrequest_id、任意のRetry-After秒数を返す。
request_idは応答から取得し、X-Request-IDは送らない（実APIのCORS契約）。
HTTPエラー本文やトークンをログへ出さない。Supabase重要テーブルの直接DMLは追加しない。

## Webからの再現

`pnpm dev:web`を起動し、`http://localhost:3000/development/mock`を開く。
合成データだけをブラウザ内のMockへ渡すため、実キー・API・DB・OAuth設定は不要。
通常、空一覧、401/403/停止/404/409/422/429/503を選んで取得・保存を実行できる。
空白の表示名で入力不正、正常名の保存と再取得で状態保持も確認できる。
合成一覧は1ページ2件、不透明cursorで次ページへ進む。シナリオ変更時は状態を初期化する。
この画面は開発用の契約確認であり、本人確認や実API認可の受け入れではない。

## ローカルHTTP Mock

`pnpm dev:mock`で127.0.0.1:8001に合成データ専用サーバーを起動する。
`MOCK_SCENARIO=401|403|inactive|404|409|422|429|503|normal|empty`で切り替える。
Bearerは合成文字列を使う。認証の安全性を検証するサーバーではなく、本番へ配備しない。
`GET/PATCH /api/v1/users/me`、`GET /__mock/profiles?limit=2&cursor=...`を利用できる。
プロフィール更新は生成OpenAPIの型/必須項目/余剰フィールド/長さ制約と、#7の前後空白除去・制御文字拒否に合わせる。
Mockは各factoryの状態を分離する。HTTP Mockは単一の合成ユーザー用なので実情報を送らない。
429はRetry-After、全応答はrequest_id/no-storeを返し、CORSで応答ヘッダーを公開する。

## 再生成と検証

CIのAPI contractジョブは4つの生成物の追跡と再生成差分を検査する。
WebジョブはMock応答を同じOpenAPIで検証し、正式/previewのuser契約一致、入力失敗、
ページング、認証切れ、PC/375pxでの全シナリオを検証する。

```bash
pnpm api:generate
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
```

人格・会話・通知等の本番業務APIは各依存Issueの範囲。
合成一覧を本番ユーザー一覧として公開しない。外部OAuth、LLM、Discordと本番ネットワークはこのテストで検証しない。

## #9 SSR認証

#2/#7とSSR認証（PR #45）はmainへ統合済み。#8の正式契約・合成Mockと併用する。
#5のトークンは未提供。
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

後続のDraft PRは依存差分をレビューするためbaseを積む。
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
