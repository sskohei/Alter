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

人格・会話・通知等の本番業務APIとSSR認証画面は各依存Issueの範囲。
合成一覧を本番ユーザー一覧として公開しない。外部OAuth、LLM、Discordと本番ネットワークはこのテストで検証しない。
