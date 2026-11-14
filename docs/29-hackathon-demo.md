# 発表用のWSL・Vercel・Quick Tunnel

2026-11-14、ユーザーの発表用途への変更に合わせて1環境で接続する。
正式本番の#33/#34/#35/#37の完了とは区別し、JWT・招待・所有権・本人承認・交流/Discordの同意は維持する。

## 実際の配置

- Web: Vercel Hobby、`sskaki/alter-hackathon-demo`、`https://alter-hackathon-demo.vercel.app`
- DB/Auth: 発表用Supabaseの1プロジェクト（秘密管理ファイルのURLを参照）。全migrationを適用。
- API: WSL、loopback 8001、reloadなし・単一worker・アクセスログなし。
- APIの中継: Quick Tunnel。今回のURLは秘密管理領域`~/.config/alter/demo/tunnel-url.txt`。
- Worker/Scheduler: APIと独立。設定だけ用意し、有料LLM呼出・交流の自動実行はまだ無効。

Quick Tunnelは一時URLで、停止/再起動時に変わる。稼働保証はなく、Windowsのスリープ・WSL停止・回線断でも使えなくなる。
発表中は電源・スリープ・会場回線を確認し、PCと各プロセスを起動したままにする。
追加の対話認証はVercelのAPI接続を妨げるためトンネルへ設定しない。アプリの認証/招待はAPIが実施する。

## 秘密と起動

秘密はrepo外`~/.config/alter/demo`（dir 700、files 600）。値を表示/コミットしない。
入力用`supabase.env`と`worker.env`から、役割別`api.env`/`runtime-worker.env`/`runtime-scheduler.env`を作る。
暗号鍵と再送HMAC鍵は独立した乱数で作り、再配備でも既存値を保持する。
Webへ渡すのは`vercel.env`の公開接続値だけ。API/DB/LLMの秘密を配布しない。
`.vercelignore`でローカルenv・venv・ログ・私的証跡をソースuploadからも除外する。

repo rootで、別ターミナルから起動する。

```bash
uv run --locked --all-packages python scripts/run_demo.py api
uv run --locked --all-packages python scripts/run_demo.py tunnel
```

ポート8001のAPIが既に動いている場合は二重起動しない。作業時の起動PID・秘密を含み得るログは
`~/.config/alter/demo/state`へ保存する。停止は該当PIDへSIGTERMを送り、API/Workerの終了を待つ。
`/health`は生存、`/ready`は必要migrationの適用、未認証`/api/v1/users/me`は401を確認する。

有料生成を有効化する前に送信対象・同意文面/版・費用上限を確認する。
キーの入力だけで有効化しない。本人がUIで承認/交流同意し、相手側の同意も揃った後にだけ予約する。

```bash
# runtime-worker.envのLLM_ENABLED=trueと、同意/予算/対象の確認後だけ実行
uv run --locked --all-packages python scripts/run_demo.py worker
# 交流ONの両参加者が揃った後、予約を1回実行
uv run --locked --all-packages python scripts/run_demo.py scheduler
```

## Vercelの設定

`ALTER_DEPLOYMENT_PROFILE=hackathon`を明示した場合に限り、ビルド検査の
「本番/Previewの別環境」と「正式本番の承認SHA」の条件を省略する。
HTTPS、Publishable/anonキー、公開変数allowlist、backend秘密の混入拒否、Web Origin登録は維持する。
この値を付けない配置の既存検査は従来どおり。`apps/web/vercel.json`の検査を削除しない。

`config/web/hackathon/.env.example`が公開変数のテンプレート。
Root Directoryは`apps/web`、Node 24.x、Root外ソースを含める設定は有効、公開システム変数の自動露出は無効。
Git自動デプロイは停止したまま。CLIでrepo rootをlinkし、`vercel --prod --yes`で手動配置する。
ここでのVercel production targetは発表用の固定aliasに割り当てるためで、正式本番受け入れを意味しない。

トンネルURLが変わったら、VercelのAPI_URL/NEXT_PUBLIC_API_URLを同じ新URLへ更新して再buildする。
Webの固定URLが変わらなければSupabase callbackとCORSを変える必要はない。

## Supabaseで残る本人操作

Google/GitHub providerは初回接続時にどちらも無効だった。
provider側Appのcallbackを`https://<project-ref>.supabase.co/auth/v1/callback`へ登録し、
Supabaseの各providerへClient ID/Secretを設定する。
SupabaseのSite URLを`https://alter-hackathon-demo.vercel.app`、Redirect URLsを同Originの
`/auth/callback`と`/auth/reauth/callback`に登録する。
これらの管理設定にはSupabaseの管理画面ログインか管理用access tokenが必要で、
アプリのSecret Keyだけでは変更できない。OAuth後に本人を照合して運営CLIで招待を登録する。
アイコンStorageキーは未設定のため、アイコンのアップロード/署名は未受け入れ。

## migrationと保存

13本を全てdry-run後にCLIで一方向適用した。接続はCAとhostnameを検証したsession pooler。
通常アプリはtransaction pooler、自動prepare無効、各pool 2/overflow 0。
Supavisor利用時、`pg_stat_ssl`はpooler→DB側の接続でありPC→poolerのTLS検査には使わない。
probeはpsycopgの実接続`ssl_in_use`と設定上のverify-fullを確認する。

適用前にpublic/Auth/StorageのDB dumpを取得し、暗号化と復号一致を確認した。
暗号文は`~/.local/share/alter-demo-backups`、鍵は`~/.local/share/alter-demo-backup-keys`。
両方とも同じPC上のためPC故障対策は未完。DB dumpはStorage本体・OAuth秘密・人格暗号鍵のbackupではない。
別媒体の暗号化保存・保持/削除・復元演習は#36の残件。実データの完全復元を保証しない。

## 受け入れ境界

接続/配置の成功と、実OAuth・招待・人格作成・双方同意・会話完了・Discord公開の成功は別に記録する。
実LLM/Discord通信は今回の自動検査では実行しない。未設定のAuthや未確認の保持条件をMockへ退避して隠さない。


## 接続検査の結果（2026-11-14）

- デプロイ: `dpl_kBEzZ9x7Y8WNVDzQMTEKoayqz91v`、ソースSHA `82b00d25ff4bcf18799c2d52e84bcd6a7b71a6c7`、READY。
- 固定Web URLの `/`・`/login` は200、`/dashboard`はログインへ307、未認証BFFは401。
- OAuth取消callbackは同じWeb Originのエラー画面へ307、認証関連応答はno-store。
- APIのローカル/Quick Tunnelのhealth/readinessは200、未認証本人APIは401。
- 実URLで`web:smoke`成功（許可/拒否CORS、未認証拒否、OpenAPI、callback/cache）。
- Auth管理キーの接続は200。Authユーザーはまだ存在せず、両OAuth providerは無効。
- Vercel上でのログイン後SSR/BFF→API、本人/他人の認可、実会話生成・Discord配送は未実施。
- 検証: Ruff/mypy、Python全体385 passed/291 DB skipped、追加TLS回帰2件、deployment 81件、
  API再生成差分なし、TypeScript、ローカルWeb build、全履歴Gitleaks。
