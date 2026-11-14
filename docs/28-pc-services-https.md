# PCサービス管理とHTTPS（#34）

2026-11-14時点では運用PCのOS・ドメイン・Supabaseプロジェクトが未準備。
この実装はLinux/systemd用の配備テンプレートであり、OS変更・サービス登録・外部公開はまだ行っていない。
責任者B（sskohei）が実PCで以下を確認してから #34 の受け入れを記録する。
Windows/macOSの場合はサービス方式を再検討し、Linux用ファイルをそのまま適用しない。

## 配備条件と配置

#33の全migration/OAuth/DB接続、#20/#28/#30/#32の配備対象実装、#36の復旧手順、#37公開ゲートを確認する。
未完のBot配信/パージを「サービスが起動した」だけで完了扱いにしない。OS更新・画面ロック・ディスク暗号化を行い、
ソースと別の暗号化保存先に環境ファイル/暗号鍵をbackupする。実行ユーザーにsudo権限を与えない。

| 環境 | Unixユーザー | checkout | 秘密 | API port |
|---|---|---|---|---|
| Preview | alter-preview | /opt/alter/preview/current | /etc/alter/preview | 8001 |
| Production | alter-production | /opt/alter/production/current | /etc/alter/production | 8000 |

環境ごとに専用systemユーザーを作り、checkoutは管理者所有・実行ユーザー読取専用にする。
rootでuv installを実行せず、配備担当が各checkoutで`uv sync --all-packages --locked`を実行し、
Python実体を専用ユーザーから読める配置にする（担当者のhome配下のuv PythonはProtectHomeで読めない）。
`UV_PYTHON_INSTALL_DIR=/opt/alter/python`など、管理した共有Pythonを配備時に使う。
サービス実行時はインストール済み`.venv/bin`を直接起動し、ネットワーク依存のsyncやreloadは行わない。

運用checkoutにはroot `.env`を作らない。`config/pc/<environment>/*.env.example`を参考に、
API/Worker/Bot/Scheduler/Purge/Opsごとに別の`/etc/alter/<environment>/<role>.env`を作る。
Purge（#30、ADR-018）にはAuth/Storageの管理鍵（`SUPABASE_SECRET_KEY`・`SUPABASE_STORAGE_SECRET_KEY`）だけをAPIと共有し、人格の暗号鍵・再送HMAC鍵・Discordの秘密・LLM/Botの鍵は置かない。Ops（#36の`alter-ops status`）は読み取りだけで、DB接続と`MAX_DAILY_COST_USD`以外の鍵を置かない。どちらも起動時の検査で、置いてはいけない鍵が入っていれば起動しない。
ファイルはroot所有600（systemdが読み、対象プロセスにのみ渡す）、親dirはrootと対象環境groupだけが辿れる750。
CA証明書は対象環境で読取可、tunnel JSONは対象環境ユーザー所有600とする。
テンプレートのREPLACE、example.invalidは起動前検査で拒否される。shellのsourceや値のログ表示はしない。
`ALTER_PROJECT_REF`/`ALTER_OTHER_PROJECT_REF`をそれぞれ逆の組み合わせにして環境混同を検査する。
DBの接続先・TLS・API issuer/Web Originの整合と、各roleへの不要秘密の混入を起動時に拒否する。
LLM/Botは既定で無効。live有効化前に対象・同意・費用上限を確認する。

## サービスの登録と起動

`deploy/systemd/alter-*.service`とtimerを管理者が`/etc/systemd/system/`にinstallしてdaemon-reloadする。
自動installスクリプトは用意せず、対象OS/ユーザー/絶対パスを実PCで照合する。
`systemd-analyze verify /etc/systemd/system/alter-*@.*`で構文と実行ファイルを検査する。

```bash
# Previewから実行。Productionはinstanceをproductionに変える。
sudo systemctl daemon-reload
sudo systemctl enable --now alter-api@preview.service
curl --fail http://127.0.0.1:8001/health
curl --fail http://127.0.0.1:8001/ready
# readiness成功、環境分離、#33のTLS/probe確認後に続ける
sudo systemctl start alter-worker@preview.service
sudo systemctl start alter-bot@preview.service
sudo systemctl enable --now alter-tunnel@preview.service
# 生成対象/費用/課金不明の照合後、最後に定期実行を有効化
sudo systemctl start alter-scheduler@preview.timer
systemctl list-timers alter-scheduler@preview.timer
# 削除のパージ（毎時）と状態確認（15分ごと）。生成/配送とは独立しているので先に有効化してよい
sudo systemctl enable --now alter-purge@preview.timer
sudo systemctl enable --now alter-ops@preview.timer
systemctl list-timers 'alter-purge@preview.timer' 'alter-ops@preview.timer'
```

`/health`は生存確認、`/ready`はliveとcheckoutが必要とするDB全migrationの適用だけを確認する。
Auth provider/LLM/Discordまでのreadyを意味しない。503にDBエラーを載せず、応答はno-store。
サービスは独立しておりBotの停止でWorkerは停止しない。Workerは1プロセス、APIは1workerでloopback固定。
SIGTERM/control-group停止、90秒の終了猶予、失敗時15秒間隔・5分内3回の起動制限を設定する。
設定不備（exit 78）は自動再起動しない。制限後は原因を直して`systemctl reset-failed <unit>`する。
LLM/Bot無効時の正常終了は自動再起動しないため、有効化後は明示的にstartする。

アプリ/外部SDKのログに本文・秘密が入る可能性を避けるため、初期配備ではstdout/stderrを破棄し、
systemdの起動/終了状態とDBの監査・ジョブ状態を使う。API access logも無効。
障害調査は秘密を表示せず、状態/エラー分類のみを記録する。ログredactionの受け入れ後に出力先を変更する。
`systemctl show <unit> -p ActiveState -p Result -p ExecMainStatus -p NRestarts`で状態を確認する。

Schedulerは`OnCalendar=*-*-* *:00/15:00 UTC`、`Persistent=false`で停止枠を再生しない。
oneshotとflock、DB一意制約で重複を抑止する。直接CLIでOSロックを迂回しない。
手動実行も`systemctl start alter-scheduler@preview.service`を使う。
timerはstartで今回の稼働中だけ動き、enable後はOS起動時にも動く。
#36の復旧演習承認後にWorker/Bot/serviceとScheduler/timerをenableする。
timerの次回日時と実行終了状態を実機で確認する。

Purge（`alter-purge@.timer`）は毎時（`OnCalendar=hourly`、最大5分ずらす）に`alter-api-purge --max-jobs 20`を1回実行する。
`Persistent=true`で、PC停止中に逃した回は起動後に1回だけ実行する（削除の30日の期限を延ばさないため）。
削除ジョブは15分のリースとskip lockedで重複しないが、flockでも同時実行を防ぐ。出力（削除要求IDを含む）は破棄する。
結果は`alter-ops status`の`deletion_failed`・`deletion_near_deadline`と削除要求の状態で確認する。

Ops（`alter-ops@.timer`）は15分ごと（Schedulerと重ならない毎時5/20/35/50分、UTC）に`alter-ops status`を実行する。
出力は件数と固定の警告コードだけなのでjournalに残す（`journalctl -u alter-ops@preview -n 20`）。
警告があると終了コード3でunitが`failed`になる。`systemctl --failed`か
`systemctl show alter-ops@preview.service -p Result -p ExecMainStatus`で確認し、
[運用手順書](27-operations-runbook.md)の警告コードごとの対応に進む。運営者への通知（メール等）の方法は未定。
`--env production`は「DBの接続先がローカルでない」確認で、Preview環境でも同じ指定になる。照合系の書き込みコマンドは定期実行しない。

## 公開HTTPS経路

候補はCloudflareのlocally-managed named Tunnel。`config/cloudflared`は環境ごとの固定hostname→
loopback API一個とcatch-all 404だけを定義する。外向き接続を使い、PCのDB/SSH/Studioへの転送は作らない。
Cloudflare accountと管理済みドメインが必要。Quick TunnelはURLが変わり開発専用のため公開受け入れには使わない。
ドメイン未所有のため固定費$0は未確認。取得/更新費・無料プラン条件・使用量・DNS移管影響を記録してから採用する。
有料契約やドメイン購入はこの変更では行わない。

1. 管理端末にcloudflaredを公式手順でinstallし、検証した版と配布元/checksumを記録する。
   systemdの実行パスは`/usr/local/bin/cloudflared`。自動更新を止め、更新もPreview先行で行う。
2. 管理端末で`cloudflared tunnel login`後、環境ごとに`tunnel create alter-preview`等で別tunnelを作る。
   account管理用cert.pemは実行PCへ配布しない。対象tunnelのJSONだけを600で所定の場所へ置く。
3. テンプレートのUUID、credentials path、hostnameを実値へ置換し、
   `cloudflared tunnel --config /etc/alter/preview/cloudflared.yml ingress validate`で確認する。
   `cloudflared tunnel route dns <uuid> <hostname>`はDNS変更の影響を確認して実行する。
4. API経路にCloudflare Accessの対話ログイン、browser challenge、cache everythingを適用しない。
   認証はAPIのSupabase Bearer/招待/所有権検証が担当する。Authorization/OPTIONSをそのまま通し、
   API全体のCacheをbypassにする。metricsはloopbackのみ。APIは転送元IPを認可に使わない。
5. PCと別端末およびVercel SSR/BFFから`/health`/`/ready`、未認証401、
   許可Origin OPTIONSと不許可Origin、他環境JWT拒否、本人/他人の所有権を確認する。
   [Web手順](25-web-deployment.md)の`web:smoke`も実行する。認証あり検証のtoken/HARは共有しない。
6. WebのAPI_URL/NEXT_PUBLIC_API_URLと環境照合用URLを揃えて再buildし、
   PreviewがProduction DB/APIへ到達しないことを確認する。callbackはWeb/Supabaseのまま。

公式根拠（2026-11-14確認）：[Tunnel開始条件](https://developers.cloudflare.com/tunnel/get-started/)、
[ingress設定](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)、
[Quick Tunnelの制約](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。
選定時にstripe-directoryでCloudflareを検索し、実構成は公式資料で照合した。

## 停止・復旧とrollback

稼働時間中はOS設定で自動スリープを無効にし、PC再起動/画面ロック時のサービス継続も実機で確認する。
停止予定と削除依頼の私的窓口を参加者へ案内し、停止で削除期限を延ばさない。

1. `systemctl stop alter-scheduler@preview.timer`で新規定期実行を止め、scheduler serviceの終了も待つ。
2. `systemctl stop alter-worker@preview.service alter-bot@preview.service`でSIGTERMを送り、進行中処理の終了を待つ。
   Botの安全な終了・配送照合は#28/#36で確認する。timeoutで強制終了した場合は結果不明として扱う。
3. API→tunnelの順でstopする。計画停止が再起動をまたぐ場合はtimer/各serviceをdisableする。
   再起動後もdisableしてあることを確認し、照合前の自動再開を防ぐ。
4. 復帰時はUTC/NTP・回線・TLS・DB/migration版を確認し、#36の独立台帳で失効/削除/課金/配送を照合する。
   通常のOS再起動はenable済みサービスが自動再開するため、実データ運用での予期しない再起動後の
   生成/配送再開方針は#36で承認するまではWorker/Bot/timerをenableしない（startだけで演習する）。
5. 失効leaseは既定のDB規則へ任せ、ジョブ/予約を一括削除しない。課金/投稿結果不明を再課金/再送/解放しない。
6. 確認後API→Worker→Bot→tunnel→timerを再開する。過去枠の一括再生をしない。
   rollbackは停止後に環境別currentを互換性のある検証済みSHAへ戻し、DBを巻き戻さずreadyを再確認する。

## #34/#36/#37へ残す実機記録

| 項目 | Preview | Production |
|---|---|---|
| OS/専用ユーザー/更新・暗号化/スリープ設定 | 未準備 | 未準備 |
| SHA/uv.lock/全migration/秘密分離・TLS/IPv4 | 未実施 | 未実施 |
| tunnel提供条件/版/固定Origin/費用・ドメイン更新費 | 未確認 | 未確認 |
| 別端末/Vercelのready・JWT/招待/所有権・CORS・他環境拒否 | 未実施 | 未実施 |
| UTC周期/flock/非catch-up・独立再起動/再起動後復帰 | 未実施 | 未実施 |
| ブラウザを閉じて会話完了・Bot停止/復帰後の許可配送 | 未実施 | 未実施 |
| PC停止/回線断/lease/課金・配送不明/期限内パージ | 未実施 | 未実施 |
| #36照合・backup復元・rollback先・担当・日時 | 未実施 | 未実施 |
