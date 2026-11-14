# 3人MVPの受け入れ記録と公開判定（#37）

2026-11-14: 初回は招待済み3人全員で受け入れる（招待上限10人は維持）。
最新の#35と正式業務APIを統合済み。#33/#34/#35/#36/#32と交流/配送の実接続、#4の法務確認が必要。
本PRのMock/axe成功は実環境受け入れや本番公開の証跡ではない。公開は実施しない。
[Done判定](21-mvp-definition.md)の全8条件を検証する。
招待契約はmainのADR-014（DB＋運営CLI）に合わせる。先行Draftの古いenv方式を採用しない。

## 自動回帰（Mock専用）

`pnpm install --frozen-lockfile && pnpm build && pnpm test:e2e`を使う。
外部OAuth/Discord/LLMは合成応答で、アプリの認証バイパスは追加しない。

| テスト | 確認すること | 実環境で別に必要な確認 |
|---|---|---|
| acceptance.spec.tsの10合成ID | Google/GitHub UI入口、作成/承認、初期OFF、予算、明示ON、キーボード停止、未知ID拒否 | 3人全員の実Auth/招待DB、他の実ユーザーIDでの所有権認可 |
| 同ファイル375/320px | 主要11画面のaxe WCAG A/AA、横溢れ、削除dialogのfocusと取消 | 読み上げ・ズーム・実機・axe incompleteの手動確認 |
| agent/auth/interaction.spec.ts | PKCE UI、入力/競合/429/障害、同意分離、停止、logout/アカウント切替 | 外部OAuth・JWT署名/失効、Workerの実停止と費用上限 |
| activity/memory.spec.ts | 履歴/通知/出典/記憶削除、ブロックと再読込後の状態 | 実ジョブ完了、削除再投入防止、双方の共有データ非表示 |
| discord/account.spec.ts | state再利用拒否、解除、fresh同一本人、削除受付と状態 | 実secret交換、招待失効/配信取消の原子性、パージ |
| deployment-security.spec.ts | JS無効でもOAuth→logout、private/no-store、確認cookie破棄 | 配信JS/Source Map/ログの秘密混入なし、実CDNキャッシュ分離 |
| scripts/*.test.mjs | 環境検査/スモーク/受け入れ判定器の失敗条件 | 外部サービスに接続した受け入れそのもの |

axe結果とincompleteはPlaywright attachments/JSONファイルに残し、CIのmock-ui artifactsへ7日保存する。自動検査だけで読み上げ確認をpassにしない。
実行対象は[Playwright公式手順](https://playwright.dev/docs/accessibility-testing)に従う。

## 実環境の準備・証跡

1. B/F/Dと公開担当・rollback担当を決め、#33/#34/#35のstaging、#36の監視、#32の横断検証を揃える。
   まず`pnpm web:smoke`を通す。合成MockをstagingのAPIへ置かない。
2. 実招待は運営CLIで本人照合後に登録する。公開レポートにはtester-1等の別名だけを用い、
   氏名・メール・provider subject・JWT・OAuth code/state・秘密・人格本文は書かない。
3. 下記の`acceptance:run init`で無視対象の`artifacts/acceptance/<run>/`に記録を作成する。
   実施したcommit SHA、UTC日時、確認担当の別名、文面版、公開/rollback担当を記録する。
   同じディレクトリへサニタイズ済み証跡を保管し、各証跡の相対pathとSHA-256を登録する。
   実記録はGit/公開PRに添付せずアクセス制限した保管先でレビューする。
4. 各caseは下表の全項目を実測してからpassへ変える。未実施/失敗はnot_run/failのまま残し、
   原因・担当・再検証日を証跡に書く。日時/commitが古くなったら新しい実行で証跡を更新する。

| check ID | 実行・残す証跡 |
|---|---|
| oauth_invitation | Google/GitHub両方の実OAuth、招待外/削除済みの直接APIと再OAuth拒否。ユーザーを勝手に作成/復活しない |
| participant_operations | 3別名×作成/承認/ON/OFF/予算/ブロック/履歴/通知/記憶削除/連携解除/アカウント削除の結果表。1人の失敗を全体passにしない |
| autonomous_browser_closed | 両者ON・未ブロック・予算内でブラウザを閉じても1会話生成、参加者だけが全文閲覧 |
| dual_consent_discord | テストギルド名/ID/公開先、両者ギルド同意＋全体ON＋管理者設定/権限の真偽組合せ、同一会話をWebとギルドで確認 |
| ownership_direct_api | 別実ユーザーの人格/記憶/会話/通知IDへの直接取得/更新拒否。anon/authenticatedのDB直接操作拒否 |
| independent_process_recovery | 手元PCのAPI/Bot/Worker/Schedulerの独立稼働とUTC 15分定期実行。PC停止/スリープ/回線断からの復旧、利用時間/停止制約の説明。Bot停止中の会話継続、復帰後の配送照合、ブラウザ非依存 |
| duplicate_cron_worker | Cron重複/Worker lease切れ・再実行・応答喪失。二重会話/投稿0 |
| unknown_delivery_hold | Discord送信成功後応答喪失・結果不明をunknown保留し、自動再送0。運営照合と解決の監査 |
| stop_and_budget | 停止後の新規LLM呼出0、日次/1会話上限・予約残高・429/外部障害時の上限遵守 |
| deletion_and_external_retention | fresh本人確認、招待失効・AI停止・未配信取消、共有会話の双方非表示、保持/外部コピーの説明と実削除 |
| mobile_axe_keyboard_screen_reader | 375/320pxの主要操作、axe結果＋incomplete、keyboard focus順/罠なし、VoiceOver/NVDA等の担当・環境・読み上げ結果 |
| legal_retention_provider_pricing | 規約/プライバシー、招待識別情報、18歳以上確認、保持期間、バックアップ/外部保持、LLM送信条件、サービス料金/有料席の版・担当・日付 |
| invitation_cli | 環境確認→本人照合→登録/一覧/identity追加/失効。識別情報がGit/env/公開ログへ出ない |
| invitation_rls_limit_identity | 招待2表のanon/authenticated直接CRUD拒否、Google/GitHub併用1人、並列登録10人上限、重複拒否 |
| invitation_fail_closed_atomic_revoke | API毎リクエスト照合、招待外403、DB/Auth障害503と副作用なし、失効/AI停止/未配信取消が同一transaction |
| purge_reoauth_restore | 失効/削除要求から早い方の30日期限のパージ、古いJWT/再OAuthで自動復帰しない、復元後に失効/削除を再適用 |
| performance_measurements | 通常読取20件以上のmethod/path/status/時間の匿名化分布とp95≤1000ms、予約→完了の全件最大≤900秒。下記metricsの原データ |
| web_security_callbacks | 両OAuthとDiscord callback、CORS許可/拒否、401復帰、logout/別人、SSR/CDN分離、配信JS秘密混入なし |
| monitoring_rollback_handoff | 費用/エラー/unknown監視を発火させて確認。公開担当・連絡経路・rollback先/停止・復旧/削除再適用手順を担当間で実施 |
| production_smoke | preproduction合格後だけ限定本番へ適用し、両OAuth/callback/CORS/401/logout/テストギルドを実確認。最終公開前に記録 |

公開必須ゲートの期限は**2026-11-17または本番公開前日の早い方**。法務等の未決定は公開担当へ引き継ぎ、未完了なら公開を延期する。

## 実行用レコードと測定

```bash
mkdir -p artifacts/acceptance
pnpm acceptance:run init artifacts/acceptance/<run>
```

現在のGit HEADを記録し、全項目未実施のreport.json、participants.json、performance.jsonを作る。
ディレクトリは0700、ファイルは0600で作成し、既存runは上書きしない。共有保管先のアクセス権も担当者が確認する。
`participants.json`は3人×12操作の結果表。report.testersと同じ別名を使い、実施後だけ各操作をpassへ変更する。
削除・解除は最後に実施し、他の操作を先に確認する。Google/GitHub両方を3人の中で検証する。
合成Mockの10ID回帰は広めの自動検査として維持し、実参加人数の証跡と分ける。

通常読み取りは、stagingで3人のうち1人が作成/承認した後に測定する。
`ALTER_ACCEPTANCE_API_ORIGIN`と`ALTER_ACCEPTANCE_TOKEN`へstaging API Originと当人の短命access tokenを
私的なシェルから渡す。トークンをコマンド引数・履歴・Git・公開ログへ残さない。

```bash
pnpm acceptance:run measure artifacts/acceptance/<run>/reads.json
```

HTTPS APIへ本人/AI/会話一覧/通知一覧のGETを計20回、リダイレクト・自動再試行なしで実行する。
本文・ヘッダー・実IDは保存せず、method/path/status/duration_msだけを保存する。
応答本文の受信完了までを計測する。失敗も除外せずstatus=0や実statusを記録し、非0終了する。
APIの通常GETを測るもので、BFF/CDNや全利用者の負荷検証は別途必要。

reads.jsonのreads配列をperformance.jsonへ移す。予約→完了は対象時間帯の**全件**をDB/監視から抽出し、
実IDをconversation-1等へ置換してreservationsへ記入する。遅い/失敗/未完了を除外しない。
全件の抽出方法・時間帯は別証跡へ記す。各予約の形は以下。

```json
{"alias":"conversation-1","status":"completed","reserved_at":"2026-11-14T01:00:00Z","completed_at":"2026-11-14T01:10:00Z"}
```

```bash
pnpm acceptance:run metrics artifacts/acceptance/<run>/performance.json
```

出力4項目をreport.metricsへ記入する。p95はnearest-rank（昇順ceil(N×0.95)番目）。
20件以上の成功した通常GETと完了済み予約1件以上が必要で、失敗/未完了は先に解決して全件を再検証する。
安全性5カウンターは対応caseの実観測から記入し、未確認を0と推測しない。
`participant_operations`の証跡pathはparticipants.json、`performance_measurements`はperformance.jsonとし、
実施後のSHA-256を登録する（`sha256sum <file>`）。case自体と日時/担当も実施後にpassへ更新する。
判定器は3人全員の全操作と原データを解析し、集計値の不一致、操作欠落、失敗要求、未完了予約を拒否する。
他のcaseにも別途サニタイズ済み実証跡が必要。

## 判定器と段階的公開

`metrics`には実測値を記入する。停止後新規呼出、予算超過、二重会話、二重投稿、不明時自動再送はいずれも0が必須。
`read_samples`は20以上、`reservation_samples`は1以上。原データの対象時間帯と抽出方法も証跡へ書く。
現在のテンプレートは全case未実施・値nullで、実行すると意図どおり`NOT READY`と非0終了になる。

```bash
pnpm acceptance:gate --report artifacts/acceptance/<run>/report.json --commit <実際の現在SHA> --phase preproduction
# 公開担当が証跡をレビュー後、#35のSHA限定buildで限定本番スモークを行う
pnpm acceptance:gate --report artifacts/acceptance/<run>/report.json --commit <同じ現在SHA> --phase release
```

既定はrelease。preproductionだけproduction_smokeを除外する。両方ともlive出典、対応環境、同一SHA、
7日以内の実行/証跡、担当、3人全員と両provider、全case、実ファイルとhash一致を要求する。
証跡はreportのディレクトリ内のみ読み、外部へのpath traversal/symlinkを拒否する。
別SHAへ修正した場合は再実行する。Mock結果をlive欄へ転記しない。

判定器は記録の充足性検査であり、入力された証跡の真実性や法務判断を保証しない。
自動CIはMockと判定器自体を検査し、live合格を捏造しない。最終承認はB/F/Dと公開担当が実証跡を確認する。
release合格後に招待者へ案内し、一般募集はしない。復旧は[Web手順](25-web-deployment.md)と#36に従う。

今回の実施結果：Mock UIと判定器の検証のみ。実3人、実OAuth/Discord、PC独立稼働/停止/復旧、
実測p95/予約完了時間、法務/料金確定、限定本番スモークは未実施で公開不可。


## Issue #37をcloseする条件

PR #54のコード/Mock検査完了後も、未実施項目はOpenのまま追跡する。
3人の実受け入れ、Done全8条件、法務文面の確認、PC停止復旧/無料枠条件、preproduction判定、
限定本番スモーク、release判定、B/F/Dと公開/rollback担当のレビューを揃えてからcloseする。
公開PRにはサニタイズ済みの結果概要とアクセス制限された証跡保管先へのリンクを記録する。
