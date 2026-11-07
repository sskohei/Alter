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
Bearer転送、リクエストID、一度だけの401更新・再送を提供する。
HTTPエラーの本文をログへ出さない。サーバーではユーザーごと・リクエストごとに生成する。
ブラウザへrefresh tokenを返す独自APIや、Supabase重要テーブルの直接DMLは追加しない。
