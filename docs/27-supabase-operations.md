# Supabase環境整備（#33）

本書は設定用runbook。2026-11-14時点でクラウドプロジェクト・OAuth App・実秘密は未準備。
テンプレート/ローカル検証の成功は実環境の完了を意味しない。責任者はsskohei（B）、Web callback確認はF。
証跡が揃うまで #33 はcloseせず、#36の復元演習と#37の公開ゲートへ引き渡す。

## 環境を作る

1. Supabase管理画面で既存分を含むFreeプロジェクト数、DB/Storage/転送量、休止条件を確認し、
   PreviewとProductionを別プロジェクトとして作る。上限内に入らない場合は作成せず構成/費用を再検討する。
   実データをPreviewへコピーしない。開発用Supabaseはこれらにlinkしない。
2. 環境別にGoogle/GitHub OAuth Appを作り、provider callbackを
   `https://<project-ref>.supabase.co/auth/v1/callback`へ登録する。
   Googleの公開状態/テストユーザー、GitHub callbackが対象環境に合うことも確認する。
3. Auth Site URLを固定Web Origin、Redirect URLsをそのOriginの`/auth/callback`と
   `/auth/reauth/callback`に限定する。実際のnext queryを含むcallbackも試す。
   wildcard・任意Preview URL・API Originを登録しない。[Web手順](25-web-deployment.md)と照合する。
4. Auth Signing KeysでES256またはRS256の署名鍵を有効化する。JWKSに公開鍵が現れることと、
   新規ログインで得たtokenの署名・issuer/audienceをAPIで検証する。HS256への互換経路だけで受け入れない。
   鍵のローテーション時は既存token寿命/JWKS cacheを考慮し、旧鍵の即時失効は侵害時に限定する。
5. DB証明書を管理画面から取得する。`config/supabase/.env.example`を参考に、通常アプリ用transaction
   poolerとmigration用session/directを分ける。専用環境ファイルはrepo外、所有者のみ読取可能（600）。
   APIだけへAuth管理鍵/Storage鍵/暗号鍵を配り、Worker/Bot/Schedulerへ一括コピーしない。

## DB接続と検証

通常アプリはIPv4対応Supavisor transaction pooler（6543）を使う。migration/dumpはsession pooler
（5432）またはIPv6が到達するdirect接続。`sslmode=verify-full`と`sslrootcert`でサーバーを検証する。
接続文字列のパスワードはURL encodeし、引数やログへ出さない。
SQLAlchemyは各プロセスpool 2/overflow 0を既定にし、psycopgの自動prepareを無効化する。
API/Worker/Bot/Schedulerが同時に動く場合は環境ごと最大8接続を基準に、管理接続・他環境も合算する。
実プロジェクトのpool/client接続上限を確認してから変更する。

秘密管理から`DATABASE_URL`だけを渡し、repo rootで実行する（root `.env`は作らない）。

```bash
uv run --locked --all-packages python scripts/check_supabase.py --project-ref <expected-ref>
```

probeは読取専用transactionでTLS、全migrationの版一致、SKIP LOCKEDの構文、非対称JWKSを確認し、
エラー詳細/URL/秘密を表示しない。OAuth本人確認・JWTの実署名・二接続の競合・IPv4経路の保証は別途必要。
Previewの合成データでWorker lease更新と並列claim、Scheduler二重起動を実確認する。
`tests/db`はテーブルを消すためクラウドへ向けない。使い捨てローカルDBのCIで競合テストを実行する。

## migrationを一方向適用する

責任者Bはレビュー済みcommitの`supabase/migrations`だけを適用する。DDL/RLSの正本はSQLのみ。
CIの空DB reset/lint/pgTAP/DB統合テストを通し、追加DDL→新アプリ→旧列削除を別リリースにする。

1. 対象SHAとproject-ref、適用済み/予定版を管理記録へ記す。環境とsession/direct接続先を二人で照合する。
2. 下記の暗号化バックアップを取得し、暗号文が別保存先へ到達したことを確認する。
3. 管理端末を対象の一環境だけにlinkする。`SUPABASE_ACCESS_TOKEN`と`SUPABASE_DB_PASSWORD`は秘密管理から渡す。
   `pnpm exec supabase link --project-ref <ref>`後、`pnpm exec supabase migration list --linked`で版を照合する。
   CLI linkの一時情報はGitに入れない。アプリ運用checkoutとは分ける。
4. `pnpm exec supabase db push --linked --dry-run`の適用予定をレビューし、
   `pnpm exec supabase db push --linked`でPreviewへ適用する。seed/reset/repairは使わない。
5. probeとPreviewでの実API/Workerテスト後、同じSHAのSQLをProductionへ適用する。
   #34で起動する前に全migrationを適用する。基礎migrationだけの段階はサービスを起動しない。
6. 失敗時は生成/配送を止め、適用済み版を照合して修正migrationを追加する。適用済みSQLを書き換えない。
   アプリrollbackは互換版へ戻す。古いDBへの復元で削除/失効を復活させない。

## バックアップと#36への引き渡し

Freeの自動バックアップを前提にしない。稼働日ごととmigration前にBが取得し、取得失敗時はmigrationを止める。
管理端末の暗号化ボリューム上、umask 077の一時領域を使用する。Supabase CLIの`db dump --linked`で
roles（`--role-only`）、schema、data（`--data-only --use-copy`）を分けて取得する。
CLI版をroot固定版に揃え、各dumpの終了コードと空でないことを検査する。
暗号化はOSの承認済み暗号化バックアップ機能またはage等で受信者公開鍵へ行い、
暗号文だけをPC以外の保存先へ転送する。復号秘密鍵は別の秘密管理へ保管する。
未暗号化dumpをクラウド同期へ置かない。一時平文は暗号化確認後に除去する。
保存先/暗号化手段の実設定は未実施。

DB dumpだけではStorageオブジェクト本体、OAuth provider secret、プロジェクト設定、署名鍵、
人格暗号鍵を復元できない。Authの管理schemaはCLIの既定除外/権限の影響を受けるため、
「Authユーザーも復元できる」と扱わず、#36で管理schemaの別export/importの可否を演習する。
Storageはprivate bucketごとの本体と対応metadataを暗号化して別保存し、削除対象の再出現を確認する。
プロジェクト設定は秘密を除いた設定台帳、秘密は独立した暗号化秘密管理で復旧する。

保持日数と削除方法は未確定の公開ゲート。#36で保存先のversion履歴/ゴミ箱/複製も含めて最長残存期間を決め、
[保持契約](23-invitation-consent-operations.md)と利用者説明へ記録する。それまでは実個人データの運用を開始しない。
復元は隔離環境で行い、バックアップ以後の削除要求/招待失効の独立台帳を再適用し、期限切れデータを消してから再開する。
復元中はAPI/Worker/Bot/Scheduler/中継を停止し、古いジョブを自動再課金・再送しない。

## 実施記録（秘密・identity・tokenを記載しない）

| 確認事項 | Preview | Production |
|---|---|---|
| project名/ref・Web/API Origin・SHA・担当・日時 | 未準備 | 未準備 |
| Free既存枠/容量/転送/休止条件と確認日 | 未確認 | 未確認 |
| 両OAuth成功/取消/再認証・他環境token拒否 | 未実施 | 未実施 |
| 非対称署名・JWKS・API実署名検証 | 未実施 | 未実施 |
| TLS/IPv4・pool上限・二接続SKIP LOCKED・lease | 未実施 | 未実施 |
| migration版/事前backup/適用後probe・担当B/F | 未実施 | 未実施 |
| 暗号化別保存・Auth/Storage復元範囲・保持/削除・#36演習 | 未実施 | 未実施 |

根拠（2026-11-14確認）：[接続方式](https://supabase.com/docs/guides/database/connecting-to-postgres)、
[バックアップ範囲](https://supabase.com/docs/guides/platform/backups)、
[署名鍵](https://supabase.com/docs/guides/auth/signing-keys)。プラン条件は公開直前に再確認する。
