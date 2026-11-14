# 3人の開発運営

## 責任

| 担当 | 主担当の成果物 | レビュー相手 |
|---|---|---|
| フロントエンド F | `apps/web`、`packages/api-client`利用、Supabase SSR、画面/E2E、Vercel | BがAPI契約、DがUI確認 |
| バックエンド B | `apps/api`、`apps/worker`、`apps/discord_bot`、`apps/scheduler`、DB/API/安全性、手元PCのバックエンド運用 | FがOpenAPI利用、Dが同意文言確認 |
| デザイン D | Figmaブランド/トークン/15画面/プロトタイプ、アクセシビリティ/文言レビュー | Fが実装可能性、Bが権限説明確認 |

Bに負荷が集中するため、MVPでは高度な記憶・複雑なマッチング・Webhook演出・詳細分析を後送りする。FはOpenAPIのMock server・生成クライアント・E2EでAPI契約の検証も担当し、Dは同意/公開文言と空・エラー状態を先行して渡す。API境界、データ公開範囲、削除方針、運用費上限は3人で決定し、[ADR](decisions/architecture-decisions.md)へ記録する。

## GitHub運用

`main`を常にデプロイ可能に保ち、`develop`は置かない。Issueごとに種別・番号・短い説明を含むブランチ（例: `feature/12-conversation-api`）を切る。PRは1目的、仕様/受け入れ条件へのリンク、スクリーンショット（UI）、OpenAPI/DB差分、テスト結果を記載。作成者以外1名レビューを必須とし、権限・公開・削除・予算変更はBとFの両視点で確認する。CI成功とレビュー後にsquash merge。PRの大きさは原則1〜2日でレビュー可能な単位。先に契約PR（schema/OpenAPI/Figma frame）を作り、実装PRの並行作業を可能にする。命名一覧と具体的なコマンドは [Git運用ガイド](22-git-workflow.md) を参照。

## 引き渡しと環境

- BはPhase 1初日にOpenAPIの`/users/me`・`/agents`契約とDBの最初のmigrationをPRで提示。FはMock APIで画面を並行実装。
- Dはトークンとオンボーディング/ダッシュボードのframeを先行し、画面ごとの4状態と文言をIssueに添付。Fはコンポーネント実装時にDと短くレビュー。
- `.env.example`だけGit管理し、実値は各サービスのSecret管理とチームの安全な共有手段で渡す。ローカルDBはSupabase CLI、Pythonはuv、Nodeはpnpm、ロックファイルを固定。
- IssueでAPI/DB/Figmaの依存を明記。毎週、費用・障害・安全性・未決定事項を10分確認する。ロードマップは [こちら](18-development-roadmap.md)。
