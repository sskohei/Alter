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
