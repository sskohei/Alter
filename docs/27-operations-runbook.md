# 運用手順書（Issue #36）

対象: 手元PCで動かすAPI・Worker・Bot・Scheduler・Purge（ADR-015/016）と、Supabase・Vercel。担当: sskohei（運営責任者）。B担当は手順とCLIの保守。
この文書は運営者が読む手順で、利用者向けの案内ではない。コマンドの出力・ログに本文・表示名・利用者ID・Discordのユーザー ID・鍵を含めない前提で作っている。貼り付けて共有するのも件数とコードまで。

## 1. 定期の確認（`alter-ops status`）

```
uv run alter-ops --env production --operator sskohei status          # 人が読む形
uv run alter-ops --env production --operator sskohei status --json   # 監視・記録用
```

- 読み取りだけなので本番でも確認の入力は求めない。終了コードは 0（警告なし）・3（警告あり）・1（接続先の食い違いなど）・2（引数の誤り）。
- 手元PCのOS定期実行（`alter-ops@<環境>.timer`、[PCサービス管理](28-pc-services-https.md)）で15分ごとに実行し、終了コード3のときはunitが`failed`になる。運営者への通知の方法（メール等）は未定。出力は`journalctl -u alter-ops@<環境>`で読める。`--json`の出力は日付ごとのファイルに残してよい（件数と固定コードだけ）。
- 稼働日の始めと、PCの再起動・停止からの復旧後には人が読む形で確認する。

警告コードと対応:

| コード | 意味 | 対応 |
|---|---|---|
| `jobs_waiting:<kind>` | 実行予定を30分以上過ぎたジョブがある | Worker（`conversation.*`）またはPurge（`account.purge`）が動いているか確認し、止まっていれば起動する。動いているのに減らなければWorkerのログ（固定コードのみ）を確認する |
| `lease_expired:<kind>` | 実行中のままリースが5分以上切れている | プロセスの異常終了の可能性。次の取り出しで自動回収されるので、手動で消さない。続く場合はWorkerを再起動する |
| `jobs_failed:<kind>` | 24時間以内に失敗で打ち切ったジョブ | `last_error`のコードを確認する。`account.purge`なら §5 |
| `llm_unknown_outcome` | LLMの呼び出し結果が分からない実行がある | §4。自動で再実行・解放しない |
| `budget_near_limit` | 今日（UTC）の費用の見込みが`MAX_DAILY_COST_USD`の80%以上 | §6 |
| `discord_needs_review` | Discordの投稿・DMで要確認がある | §3 |
| `discord_cleanup_failed` | 削除済みアカウントのスレッド削除が打ち切りになった | ギルド管理者にスレッドの削除を依頼する（docs/11）。`forbidden`は警告にしない（MVPのBotは権限を持たないため想定内） |
| `deletion_failed` | 削除要求が8回の再試行で打ち切りになった | §5 |
| `deletion_near_deadline` | 受付から25日以上たって完了していない削除要求がある | §5。期限は30日で延ばさない |

## 2. PCの停止と復旧

計画停止（docs/14 と同じ順番）:

1. OSの定期実行（Scheduler・Purge・`alter-ops`）を止める。
2. Worker・Botに終了シグナルを送り、進行中の処理が終わるのを待つ（新しい取り出しはしない）。
3. APIとHTTPS中継を止める。
4. 停止の予定と復旧の見込みを、招待時の私的な経路で参加者へ伝える。

予定外の停止（電源断・スリープ・回線断・OS再起動）からの復旧:

1. 時刻同期（`timedatectl`などでNTP同期済みか）・回線・DB接続を確認する。
2. migrationの版が想定どおりか確認する（`supabase migration list`、または`supabase_migrations.schema_migrations`の最新版）。
3. `alter-ops status`で件数を確認する。リース切れは自動回収に任せ、ジョブ・予約・配信行を手動で一括削除しない。
4. API → Worker → Bot → OSの定期実行 の順に再開する。Schedulerは停止中に逃した枠を再生しない（現在の枠から再開）。
5. 再開後にもう一度`status`を実行し、`jobs_waiting`が減っていくこと、§3・§4の対象を確認する。

## 3. Discord配送の要確認の照合

要確認（`needs_review`）は「Discordに書き込んだか分からない」状態。Botは自動で送り直さない（二重投稿を避けるため。docs/11）。

```
uv run alter-ops --env production --operator sskohei review-list
```

一覧には種類（post/dm）・配信ID・会話ID・チャンネルID・保存済みのスレッドID・理由のコード・試行回数・更新日時だけが出る。

投稿（post）:

1. 理由が`ambiguous_target`なら、投稿先が複数のギルドで条件を満たしたため保留した行。ギルド管理者と相談して1つ選び、次を実行する。選んだ行だけが`pending`に戻ってBotが送り、ほかは`cancelled`（`not_chosen`）になる。選んだ投稿先が条件を満たさなくなっていれば失敗する。
   ```
   uv run alter-ops --env production --operator sskohei choose-target <会話ID> <チャンネルID>
   ```
2. それ以外（`partial:*`・`unknown:*`・`stale_sending`など）は、Discordでそのチャンネルを確認する。スレッドIDが保存されていればそのスレッドを開く。
   - スレッドと発言が全部そろっていれば、送信済みとして記録する（スレッドIDと最初の発言のIDはDiscordの「IDをコピー」で取る）。
     ```
     uv run alter-ops --env production --operator sskohei resolve-post <配信ID> sent --thread-id <スレッドID> --message-id <最初の発言ID>
     ```
   - スレッドがない・途中までしかない場合は、途中のスレッドがあれば削除してから（または管理者に依頼してから）取り消す。送り直しが必要な場合も、MVPではCLIから送り直さない（取り消しのみ）。
     ```
     uv run alter-ops --env production --operator sskohei resolve-post <配信ID> cancelled
     ```

DM（dm）: Discordのユーザー ID は一覧に出さない。本人に私的な経路でDMが届いたか確認できた場合だけ`resolve-dm <配信ID> sent --message-id <ID>`、確認できなければ`resolve-dm <配信ID> cancelled`（Web通知は残っている）。

照合系のコマンドは本番では`production`の入力で確認する。どれも`needs_review`の行にしか効かず、監査には操作の種類・配信ID・結果・運営者名だけが残る。

## 4. LLMの結果不明の照合

`status`の「LLM 未確定」のうち`unknown`（`llm_runs`）と、`llm_invocations`の`unknown`が対象。Workerは結果が分からない呼び出しを再実行せず、予算の予約も保持したままにする（docs/08・09）。

1. Anthropic Consoleの使用量（日付・モデル・トークン数）と、`usage_records`の当日分を見比べる（照合はsskoheiだけが行う。APIキーはsskoheiの環境にだけある）。
2. この版では結果不明の実行を確定させるCLIはない。件数が増え続ける・費用の見込みが上限に近い場合は、Workerの`LLM_ENABLED=false`で新しい呼び出しを止めてから、Bで確定用の手順を追加する（#36の残り）。
3. 確定させる前に、該当するAI・会話を手動で削除しない（費用台帳のガードで止まる）。アカウント削除はこの間`llm_unsettled`で待つ。

## 5. アカウント削除の打ち切り・期限

- `deletion_failed`: 削除要求とジョブの`error_code`/`last_error`のコードを確認する。`llm_unsettled`なら §4 を先に行う。`auth_unavailable`・`storage_unavailable`ならSupabaseの状態と管理鍵を確認する。原因を直したら、Bに依頼してジョブを再投入する（打ち切りの解除は手動）。
- `deletion_near_deadline`: 30日の期限を延ばさない。PC停止が原因ならPurgeを手動で1回実行する（`uv run --package alter-api alter-api-purge`）。完了したら本人へ私的な経路で結果を伝える（docs/23）。

## 6. 費用の警告

`budget_near_limit`はWorkerの日次上限（`MAX_DAILY_COST_USD`、既定$1）の80%を見込みが超えたとき。上限に達するとWorker自身が新しい呼び出しを止めるので、まず放置してよい。毎日続く場合や想定（月$8〜17、docs/20）を超える場合は、Schedulerの間隔・`SCHEDULER_BATCH_SIZE`・利用者の日次予算を見直す。急に増えた場合はWorkerの`LLM_ENABLED=false`で止めてから原因を調べる。

## 7. バックアップと復元（概要、#33/#37で具体化）

- 稼働日ごと、およびmigrationの前に`pg_dump`（`SUPABASE_DB_URL`）でDBを取得し、暗号化してPCとは別の保存先に置く。鍵はバックアップと別に保管する。保存期間は運用契約（docs/23）に従う。
- 復元の前に、可能なら現在のDBから`deletion_requests`・失効した招待・解除したDiscord連携の一覧を取っておき、復元後に削除と失効を再適用してからサービスを再開する（復元で削除済みの人を戻さない）。
- 公開前に、合成データで復元演習を1回行う（#37）。

## 8. 鍵の交換

| 鍵 | 置き場所 | 交換の手順 |
|---|---|---|
| `ANTHROPIC_API_KEY` | WorkerのPC環境ファイル（sskoheiのみ） | Consoleで新しい鍵を作り、環境ファイルを差し替えてWorkerを再起動し、古い鍵を無効にする |
| `DISCORD_BOT_TOKEN` | Botの環境ファイル | Developer PortalでReset Token、差し替えてBotを再起動 |
| `DISCORD_CLIENT_SECRET` | APIの環境ファイル | Developer Portalで再発行、差し替えてAPIを再起動 |
| `SUPABASE_SECRET_KEY`・`SUPABASE_STORAGE_SECRET_KEY` | APIとPurgeの環境ファイル（ADR-016） | Supabaseで新しいsecret keyを作り、APIとPurgeの両方を差し替えてから古い鍵を削除 |
| `PROFILE_ENCRYPTION_KEY_V1` | APIの環境ファイル | 現在はV1だけ。交換（新旧の鍵で読み、再暗号化）のCLIは未実装で、#36の残りとして扱う。漏えいが疑われる場合はAPIを止めてBに連絡する |

鍵の値をチャット・Issue・コミットに書かない。交換したら日時と鍵の種類だけを運営メモに残す。

## 9. 切り戻し

- Web: VercelのDeploymentsから直前の正常な版をPromoteする。
- API・Worker・Bot: 運用用checkoutで直前のタグ（またはコミット）に戻し、`uv sync --all-packages --locked`のあと各プロセスを再起動する。
- DB: migrationは前進のみ。戻す必要があれば打ち消すmigrationを新しく作ってPRを通す（PR本文の「戻し方」を参照）。バックアップからの復元は最後の手段で、§7の再適用を必ず行う。
