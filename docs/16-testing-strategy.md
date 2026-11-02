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

CIは本番LLMや本番Discordへアクセスしない。会話文章の完全一致ではなく、発言者順・schema・上限・禁則・保存/配信条件を検査する。プロンプトインジェクションの固定データセットを持ち、相手発言から秘密開示命令を実行しないことを確認する。DB統合テストはmigrationを空DBに適用して行い、RLSとFK/UNIQUEを検証する。OAuthの外部実接続、Render→Supabaseネットワーク、Discord権限はステージングで最終確認する。

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
