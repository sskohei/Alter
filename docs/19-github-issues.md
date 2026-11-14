# GitHub Issueバックログ（実装開始順）

1 Issueは1〜2日でレビューできる粒度。ラベルは`area:web|api|db|worker|bot|design`、`phase:P1..P4`、`priority:P0..P2`。受け入れ基準をテスト可能な形で書き、依存Issueをリンクする。担当記号は [チーム運営](17-team-development.md)。

| 順 | Issue / 主担当 | 成果物と受け入れ | 依存 |
|---:|---|---|---|
| 1 | Monorepo/CI雛形 B | uv/pnpm、Ruff/TypeScript/pytest、空DB migration CIが通る | なし |
| 2 | ブランド・UIトークン D | Figmaの色/文字/余白、Button/Form/Card 4状態、PC/mobile | なし |
| 3 | AuthとユーザーAPI契約 B | OpenAPI `GET/PATCH /users/me`、401/200/所有権例 | 1 |
| 4 | Next.js SSRとログイン F | Google/GitHubのコールバック、ログアウト、401復帰のMock/E2E | 1,3 |
| 5 | DB基礎DDL/RLS B | profiles/agents/profile/settings、匿名直接DML拒否、FK/UNIQUE | 1 |
| 6 | オンボーディングFigma D | 設問、公開同意、手動入力、エラー/空/モバイル | 2 |
| 7 | AI作成/承認API B | 下書き/版/承認、未承認AIは交流不可 | 3,5 |
| 8 | AI作成・編集UI F | Mock APIで下書き→確認→承認、409/422表示 | 4,6,7の契約 |
| 9 | ダッシュボードFigma D | 状態/履歴/予算/通知の4状態 | 2 |
| 10 | 会話/ジョブDDLとScheduler B | 15分Cron、同意/ブロック/費用/同時実行で1件予約 | 5,7 |
| 11 | Worker会話生成 B | 偽LLMで8ターン上限、保存、再試行、費用停止 | 10 |
| 12 | 会話/通知API B | 参加者のみ一覧/詳細、完了通知1件 | 10,11 |
| 13 | 会話・設定画面 F | 15〜30秒Polling、停止/予算、空/エラー/モバイル | 8,9,12の契約 |
| 14 | Discord同意/連携設計 D | ギルド別公開確認、不可逆投稿注意、連携状態 | 2 |
| 15 | Discord OAuth/コマンド B | ID紐付け、pause/resume、管理者setup、未連携拒否 | 5,7 |
| 16 | Discord outbox投稿 B | 両者ギルド同意だけ投稿、停止復帰、送信不明を保留 | 11,15 |
| 17 | Discord設定UI F | 連携/解除/ギルド別同意状態とBotコマンド案内、権限・エラー状態 | 14,15の契約 |
| 18 | 10人MVP E2E F | 作成→交流→Web/Discord表示、他人データ不可、停止/復旧 | 11〜17 |
| 19 | 記憶/関係性簡易版 B | 出典つき要約、削除、回数再集計 | 11 |
| 20 | 記憶/関係性UI F | 削除確認と相手リスト、0件/エラー | 19の契約 |

招待管理の方針は2026-11-08にDB＋運営専用CLIへ変更した（ADR-014、方針管理 #4）。実GitHub Issue #6の基礎DDLは完了済みのため、招待2表の追加migration・RLS/制約テスト・CLI・共通認可は #7 へ引き渡す。削除/パージは #30、公開受け入れは #37 で追跡する。上表の「順」は計画上の順序であり、実GitHub Issue番号とは異なる。

最初の並行着手はB: Issue 1/3/5、F: Issue 4の画面骨格とMock、D: Issue 2/6/9。Bの実装待ちでもFはIssue 3のOpenAPI契約で進める。P3のDiscord公開はIssue 16を完了条件とし、Botを単に接続するだけではMVP完了としない。


## 配置方針変更の引き渡し（2026-11-14、#75）

Web=Vercel Hobby、DB/Auth=Supabase Free、バックエンド=運営者の手元PCへ変更する（ADR-015）。実GitHub Issueの #34 にある旧Render Blueprint前提は本方針で置き換える。

| 実Issue | 作業内容 / 受け入れ |
|---|---|
| #33 | 無料枠内で本番/Previewを分離。PCからのTLS/プーラー接続、OAuth、migration、バックアップ/復元対象を確認 |
| #34 | PCの独立API/Worker/Bot・OS定期実行・HTTPS公開経路を整備。プロセス別秘密、UTC周期、多重起動抑止、自動起動/再起動、URL固定可否を検証。Render Blueprintは作成しない |
| #35 | Vercel Hobbyの用途条件を確認し、環境別の公開HTTPS API URL・Auth callback・CORS・SSR疎通を設定 |
| #36 | PCの監視・スリープ/回線断/再起動・課金/配送照合・期限内パージ・別保存先バックアップ/復元を整備 |
| #37 | PC稼働中の10人MVPと停止/復旧を実環境で受け入れ、利用時間/停止制約を説明。既存の同意/公開必須ゲートは維持 |

#75は文書のみ。公開経路の提供元、PCのOS/具体的サービス定義、運用スクリプトと実接続は未実装/未検証として各Issueへ引き渡す。
