# Alter

更新日: 2026-11-14。`docs/`が実装契約。JWT/招待認可、ユーザー・交流設定・人格版/承認・アイコンのAPIとフロント先行画面を実装した。実OAuth/Storageの公開受け入れ・本番Worker接続は残件。[Mockと実接続](docs/24-frontend-development.md)を区別し、10人の招待制MVPを最初の受け入れ目標とする。

## ローカル開発（最初に実行）

前提: Git、Node.js **24.18.0**（`.nvmrc`）、pnpm **10.34.9**、uv **0.13.6**、Docker Engine/Desktop（稼働中）。Python **3.13.12**はuvが取得する。Supabase CLI **2.122.0**はpnpmで固定しており、グローバルインストールやSupabaseへのログインは不要。

```bash
# nvm利用時
nvm install
nvm use
npm install --global pnpm@10.34.9
# uv未導入時（公式インストーラのバージョンを固定）
curl -LsSf https://astral.sh/uv/0.13.6/install.sh | sh
# 新しいシェルを開くか、表示されたPATH設定を反映する
pnpm install --frozen-lockfile
uv sync --all-packages --locked
cp .env.example .env
```

以下はすべてリポジトリrootで実行する。`.env`のダミー秘密はMock時は未使用。実値をコミットしない。

### ローカルDB/Auth

```bash
pnpm db:start          # 初回はDockerイメージをダウンロードする
pnpm db:status        # URLとローカルキーを確認（出力は共有しない）
# WebのAuth実装に必要な公開キー取得（SSRログイン画面用。実OAuth登録は#33/#35が依存）
pnpm exec supabase status -o env
pnpm db:stop           # データを保持して停止
# 必要時のみ: ローカルDBのデータを削除しmigrations/seedを再適用
pnpm db:reset          # --local固定。開発データが消える
```

DB `127.0.0.1:54322`、API/Auth `http://127.0.0.1:54321`、Studio `http://127.0.0.1:54323`、メール確認 `http://127.0.0.1:54324`。DBはSupabaseの1つだけを使用する。`supabase/config.toml`は初期化済みのため`supabase init`は不要。アプリのDDL/RLSは後続Issueで`supabase/migrations/`へ追加する。OAuth供給元の設定は本Issueの範囲外で、ローカルメールAuthはSupabase API/Studioから利用できる。

### Web / API / 独立プロセス

Web用には公開変数だけをコピーする（rootの秘密をコピーしない）。

```bash
# 初回のみ
uv run python - <<'ENV'
from pathlib import Path
public = ("NEXT_PUBLIC_", "SUPABASE_URL=", "SUPABASE_PUBLISHABLE_KEY=")
Path("apps/web/.env.local").write_text("\n".join(
    line for line in Path(".env").read_text().splitlines() if line.startswith(public)
) + "\n")
ENV
```

それぞれ別ターミナルで起動する。

```bash
uv run --package alter-api alter-api              # http://localhost:8000/docs /health
pnpm dev:web                                    # http://localhost:3000
uv run --package alter-worker alter-worker      # Mock待機ループ（Ctrl-Cで停止）
uv run --package alter-discord-bot alter-bot     # 既定は無効、正常終了
uv run --package alter-scheduler alter-scheduler # Cron: 1回実行して終了
```

Webの`/development`の「APIの接続を確認」で生成TypeScriptクライアント経由の接続を確認できる。`/health`は生存確認専用で、DB接続・Authの準備完了は意味しない。APIは招待認可付きの`GET/PATCH /api/v1/users/me`を提供する。人格APIは未実装。
`/development/mock`では合成データでプロフィール・ページング・エラーを確認できる。

### Mockと無効化

既定の`ALTER_MODE=mock`ではWorker/Cron/BotのLLM・Discord処理は合成動作となる。APIの`/health`とWebはSupabase停止中でも起動確認できる。本人プロフィールAPI（`GET/PATCH /api/v1/users/me`）はmockモードではJWT検証を通過しても共通認可処理で503 `SERVICE_UNAVAILABLE`を返し、招待確認・DB接続へ進まない。

本人プロフィールの実APIを呼ぶには`ALTER_MODE=live`とローカルSupabaseが必要。有効なJWT・招待とDB接続の設定を用意し、`ALTER_MODE=live uv run --package alter-api alter-api`でAPIを起動する。外部接続なしで確認するときは[合成Mock](docs/24-frontend-development.md)を使う。Worker/Cronの`live`処理は未実装なので明示的にエラー終了する。

```bash
uv run --package alter-worker alter-worker --once
DISCORD_ENABLED=true uv run --package alter-discord-bot alter-bot --once
# MockのBot待機ループ（実際のGatewayには接続しない）
DISCORD_ENABLED=true uv run --package alter-discord-bot alter-bot
```

`DISCORD_ENABLED=false`ならBotは即時終了し、トークン不要。実接続の機能は後続Issueで実装する。変数ごとの取得先・必須/任意・対象サービスは[.env.example](.env.example)を参照。

### 検証とAPI契約の再生成

```bash
uv run ruff check .
uv run ruff format --check .
uv run --all-packages mypy
uv run --all-packages pytest
pnpm api:generate
pnpm lint
pnpm typecheck
pnpm test             # Vitest + Testing Library
pnpm build
# 初回のみChromiumとOS依存を準備
pnpm --filter web exec playwright install --with-deps chromium
pnpm test:e2e         # 本番buildを起動、PC/375pxでMock APIを検査
# API変更時は再生成し差分を同じPRに含める
# packages/api-client/openapi.json と src/schema.d.ts が生成物
```

DB統合テスト（`tests/db`）は招待2表を空にするため、ローカルSupabaseなど使い捨てDBにだけ向ける。

```bash
ALTER_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres \
  uv run --all-packages pytest tests/db
```

招待の登録・失効は運営専用CLI`alter-invitations`で行う（[招待契約](docs/23-invitation-consent-operations.md)）。

GitHub Actionsの必須チェックは`CI required`。Python/Web/API生成差分/空Supabase migration/secret scanningの検査範囲は[テスト戦略](docs/16-testing-strategy.md)、`MainRule`の承認・保護設定は[Git運用](docs/22-git-workflow.md)を参照。

OpenAPIの正本はFastAPI/Pydantic。現時点では`GET /health`と`GET/PATCH /api/v1/users/me`を生成し、その他の業務APIの将来契約は[API設計](docs/07-api-design.md)に記載する。ローカルキー・Dockerデータ・`.env`・`.next`・`.venv`はGit管理しない。lockfileは両方管理する。

## 読む順序

1. [製品とMVP](docs/01-product-overview.md)・[機能要件](docs/02-functional-requirements.md)・[MVP境界](docs/21-mvp-definition.md)
2. [技術選定](docs/03-technical-stack.md)・[構成](docs/04-system-architecture.md)・[認証](docs/06-authentication-authorization.md)
3. [DB](docs/05-database-design.md)・[API](docs/07-api-design.md)・[AI処理](docs/08-ai-agent-architecture.md)・[交流](docs/09-autonomous-interaction.md)
4. [記憶/関係](docs/10-memory-relationship.md)・[Discord](docs/11-discord-bot.md)・[画面](docs/12-frontend-screens.md)・[デザイン](docs/13-design-system.md)
5. [デプロイ](docs/14-deployment-infrastructure.md)・[セキュリティ](docs/15-security-privacy.md)・[テスト](docs/16-testing-strategy.md)・[チーム運営](docs/17-team-development.md)・[Git運用](docs/22-git-workflow.md)・[ロードマップ](docs/18-development-roadmap.md)・[Issue](docs/19-github-issues.md)・[費用](docs/20-cost-estimation.md)

実装の契約は [API設計](docs/07-api-design.md)、テーブルの正本は [DB設計](docs/05-database-design.md)。外部サービスの設定手順は [デプロイ](docs/14-deployment-infrastructure.md) に集約する。設計変更は [意思決定記録](docs/decisions/architecture-decisions.md) を更新してから各文書に反映する。開発エージェントは [AGENTS.md](AGENTS.md)（Claude Codeは [CLAUDE.md](CLAUDE.md)）も読む。

## MVPの成立条件

招待した約10人がGoogle/GitHubでログインし、本人が承認した人格と交流設定でAI分身を作成できる。サーバー側の定期処理が両者の同意・予算・ブロックを確認し、2体の会話を生成して保存する。参加者はWebで会話を閲覧でき、明示的に許可したセッションだけが設定済みDiscordチャンネルへ投稿される。ブラウザを閉じても動き、失敗後の再試行で二重会話や二重投稿を生まない。

## Claude Sonnet 5.5の採用（Issue #14）

10人MVPはClaude直接APIの`claude-sonnet-5-5`を正式採用。
共通の採用設定・単価は`alter_domain.llm_selection`、評価CLIもこの設定を使用する。
`uv run --package alter-domain alter-llm-evaluate`は外部通信なしのdry-run。
実評価はroot `.env`の`ANTHROPIC_API_KEY`と明示的な`--live`でのみ有効になる。
[限定評価](evaluations/llm/sonnet-review-2026-11-13.md)・
[評価手順](evaluations/llm/README.md)・[選定/保持条件](docs/26-llm-candidate-review.md)を参照。
残りの品質受け入れは#37、本番Workerのadapter・moderation・費用予約の接続は#18の範囲。

## アイコンAPI（#15）

`POST /api/v1/agent-icons`へPNG/JPEG/WebPのバイナリを送ると、検証済みの本人用`icon_path`を返す。
作成時または`PATCH /agents/{id}`で指定でき、GET等では300秒の`icon_url`だけを返す。
入力/正規化後とも2 MiB以下・2048px以下・単一フレーム。実画像をデコード/PNG再エンコードし、
EXIF/GPS等のメタデータを削除する。専用のprivate bucketとAPI専用Storageキーを使う。
[API契約](docs/07-api-design.md)・[Storage設定](docs/14-deployment-infrastructure.md)を参照。
