# Alter: Claude Code向け案内

このリポジトリの作業規約は [AGENTS.md](AGENTS.md) を正本とする。作業前に同ファイルと [README.md](README.md)、担当する `docs/` を読む。Git操作は [docs/22-git-workflow.md](docs/22-git-workflow.md) を参照する。

Alterは、本人が承認したAI分身同士がサーバー側で交流するWebアプリ。Next.js、FastAPI、Supabase、Vercel、Renderは確定済み。会話の保存先はPostgreSQL、認証はSupabase Auth、業務操作と認可はFastAPIに集約する。Discord Botは保存済み会話だけを、両参加者とギルド管理者の許可に基づいて表示する。

着手時には関連するAPI/DB/画面仕様を確認し、実装・テスト・文書の整合性を保つ。秘密情報や`.env`をコミットせず、既存の未コミット変更を上書きしない。仕様変更が必要なら理由と影響を [ADR](docs/decisions/architecture-decisions.md) に記録する。
