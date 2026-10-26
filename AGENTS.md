# Alter: 作業エージェント向け指示

## 最初に読むもの

- [README.md](README.md) と担当範囲の `docs/` を読む。`prompts/SPECIFICATION.md` は元の仕様書作成指示であり、アプリ本体の実装契約は `docs/` を参照する。
- Gitのブランチ、コミット、PR手順は [docs/22-git-workflow.md](docs/22-git-workflow.md) に従う。既存の未コミット変更を消さない。
- ユーザーの最新の依頼を優先する。仕様が未決定なら関連する [ADR](docs/decisions/architecture-decisions.md) とIssueに判断を記録する。

## 確定した構成

- Web: Next.js App Router、TypeScript、Tailwind CSS、shadcn/ui。認証はSupabase Auth。
- API: FastAPI、Pydantic。業務操作と所有権検証はAPIが担当し、Webから重要テーブルを直接更新しない。
- DB: Supabase PostgreSQL。DDL/RLSの正本は`supabase/migrations/`のSQLで、Alembicと二重管理しない。
- 自律交流: Render Cron、独立Worker、DBジョブ。Discord Botは独立プロセスで共通ドメイン処理とDBを利用する。
- デプロイ: WebはVercel、API/Worker/Bot/CronはRender。[構成](docs/04-system-architecture.md)、[デプロイ](docs/14-deployment-infrastructure.md)を参照。

## 変更時の約束

- APIの変更はPydantic/OpenAPIを正本にし、生成TypeScriptクライアントと関連文書を同じPRで更新する。
- 認証・公開・削除・費用上限を変更する際は本人の所有権、両参加者の同意、Discordギルドの設定を保つ。[認証](docs/06-authentication-authorization.md)、[安全性](docs/15-security-privacy.md)を確認する。
- LLMやDiscordの外部APIを使うテストはMockを基本とする。本番キーをCIやリポジトリへ入れない。`.env.example`と`pnpm-lock.yaml`、`uv.lock`はGit管理対象。
- 変更に必要なテストを実行し、結果と未検証箇所をPRに記す。既存文書と実装の契約が変わる場合は文書も更新する。
- `main`へ直接pushせず、関連ファイルだけをstageする。秘密、ローカルDB、生成ビルド成果物をコミットしない。
