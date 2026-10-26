# Alter 設計仕様

更新日: 2026-10-27。これは `prompts/SPECIFICATION.md` に基づく**実装前の設計**であり、アプリケーションコードは含まない。10人の招待制MVPを最初の受け入れ目標とする。金額・外部API仕様は各文書の出典と確認日を参照し、着手時に再確認する。

## 読む順序

1. [製品とMVP](docs/01-product-overview.md)・[機能要件](docs/02-functional-requirements.md)・[MVP境界](docs/21-mvp-definition.md)
2. [技術選定](docs/03-technical-stack.md)・[構成](docs/04-system-architecture.md)・[認証](docs/06-authentication-authorization.md)
3. [DB](docs/05-database-design.md)・[API](docs/07-api-design.md)・[AI処理](docs/08-ai-agent-architecture.md)・[交流](docs/09-autonomous-interaction.md)
4. [記憶/関係](docs/10-memory-relationship.md)・[Discord](docs/11-discord-bot.md)・[画面](docs/12-frontend-screens.md)・[デザイン](docs/13-design-system.md)
5. [デプロイ](docs/14-deployment-infrastructure.md)・[セキュリティ](docs/15-security-privacy.md)・[テスト](docs/16-testing-strategy.md)・[チーム運営](docs/17-team-development.md)・[Git運用](docs/22-git-workflow.md)・[ロードマップ](docs/18-development-roadmap.md)・[Issue](docs/19-github-issues.md)・[費用](docs/20-cost-estimation.md)

実装の契約は [API設計](docs/07-api-design.md)、テーブルの正本は [DB設計](docs/05-database-design.md)。外部サービスの設定手順は [デプロイ](docs/14-deployment-infrastructure.md) に集約する。設計変更は [意思決定記録](docs/decisions/architecture-decisions.md) を更新してから各文書に反映する。開発エージェントは [AGENTS.md](AGENTS.md)（Claude Codeは [CLAUDE.md](CLAUDE.md)）も読む。

## MVPの成立条件

招待した約10人がGoogle/GitHubでログインし、本人が承認した人格と交流設定でAI分身を作成できる。サーバー側の定期処理が両者の同意・予算・ブロックを確認し、2体の会話を生成して保存する。参加者はWebで会話を閲覧でき、明示的に許可したセッションだけが設定済みDiscordチャンネルへ投稿される。ブラウザを閉じても動き、失敗後の再試行で二重会話や二重投稿を生まない。
