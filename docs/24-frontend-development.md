# フロント先行実装とMock

#8は#3（完了）/#7（契約レビュー待ち）に依存する。
実APIの`openapi.json`は`/health`のみ。文書から起こしたレビュー用Pydantic契約は
`apps/api/src/alter_api/contracts/frontend_preview.py`を正本とし、`pnpm api:generate`で
`preview.openapi.json`/`preview-schema.d.ts`を生成する。本番APIへはマウントしない。
CIは両方のOpenAPI/型の未生成差分を拒否する。

`pnpm dev:mock`で127.0.0.1:8001に合成データ専用サーバーを起動する。
`MOCK_SCENARIO=401|403|inactive|404|409|422|429|503|normal|empty`で切り替える。
Bearerは合成文字列でよく、認証の安全性を検証するサーバーではない。本番へ配備しない。
ページングヘルパーは不透明cursorと1〜50件を扱う。業務一覧の空fixtureは対応画面の契約追加時に接続する。

`@alter/api-client/preview`は型付きクライアント、共通エラー（status/code/request_id）、
Bearer転送、応答のリクエストID、一度だけの401更新・再送を提供する。
HTTPエラーの本文をログへ出さない。サーバーではユーザーごと・リクエストごとに生成する。
ブラウザへrefresh tokenを返す独自APIや、Supabase重要テーブルの直接DMLは追加しない。

## mainの実装との統合（2026-11-10）

#43/#55でJWT検証・共通エラー・招待DB/CLIがmainへ入った。previewのエラーは
`alter_api.errors.ErrorResponse`を直接使用し、エラーコードとrequest_idの型を二重管理しない。
Mockエラーも実APIの生成OpenAPIで検査する。request_idはAPIが採番するため、
クライアントはX-Request-IDを送らず応答から取得する（APIのCORS許可ヘッダーに含まれない）。

`GET/PATCH /users/me`を含む業務ルーターは未実装で、招待判定はドメイン処理まで。
プロフィール・人格・会話等のMockを実APIへ切り替える条件はまだ満たさない。
外部OAuth/LLM/Discordを使う自動テストのMockは引き続き維持する。

## #9 SSR認証

依存は#2（完了）、#7（契約待ち）、#8（先行PR）、#5（トークン未提供）。
`@supabase/ssr`のbrowser/server clientとNext 16 ProxyでセッションCookieを更新し、
保護レイアウトとcallbackは`getUser`でAuthサーバーを確認してからAPI `/users/me`で入場判定する。
403招待外/停止、503判定障害は拒否し、OAuth成功だけで入場しない。
Callbackで`onboarded_at`がnullならonboarding、それ以外は許可した内部URLへ遷移する。
API401では1回だけ更新・再送する。画面からのAPIは同一Originの許可リスト付きBFFを通す。
全認証応答はprivate/no-store。ログアウトはSupabase signOut後に全画面遷移し、
永続Query cache/業務データのlocalStorageは使わない。

`apps/web/.env.example`に公開設定、任意のサーバー側`API_URL`とcallback Originを記載した。
実OAuthはSupabase/Google/GitHubの登録済みURLが必要（#33/#35）。
`pnpm test:e2e`は独立したローカルAuth/API合成サーバーを起動する。
アプリにテスト専用認証ルートやCookieの検証を省略する分岐はない。
法務確認済みの正式文面は未提供のため、/legalは開発用案と明示する。

Draftは依存差分をレビューするため #8 → #9 → #17 のbaseを積む。
先行PRのSquash後は残りへmainを取り込み、baseをmainに変更して再検証する。
mainへ直接push/自動mergeは行わない。依存ブランチ向けPRもCIの対象に含める。

## #17 AI分身の作成・編集

依存は#8（PR #44）、#9（PR #45）、#10（デザイン待ち）、#15（正式API待ち）。
/onboardingの3段階質問→/agent/newの手動入力→下書き保存→公開項目/差分確認→
未チェックの本人承認→/agentを実装する。/agent/editは新しい人格版を保存・承認する。
RHF/Zodで入力を検査し、生成型を満たす要求を同一Origin BFFへ送る。
Button/Input/Textarea/AlertDialogはshadcn/uiの構成（Radix/CVA/ローカルソース）を起点にする。
色・配置・文言の最終承認は#5/#10に残る。

公開可能な項目だけを本人IDと編集対象・版で区切ったsessionStorageへ保存する。
非公開欄は任意・初期空、編集時の専用APIだけから取得し、通常表示/差分/AI提案のanswers/
ストレージ/ログへ出さない。ログアウト時に下書きも削除する。私的欄の読込失敗時は
空で上書きしないよう保存を止め、再試行を案内する。

任意提案は下書き保存後だけ呼び出せる。提案の適用は本人操作で行い、再保存・再承認が必要。
ローカルAPI Mockは503を返すが、成功応答もブラウザE2Eで差し替えて検証する。
429/503でも手動入力を続けられる。409は入力を保持して最新人格の再読込を案内し、
422は入力上限の確認、未保存変更は承認を無効化し離脱時に確認する。

契約レビューで確認する点（#7/#15）:

- Agent名の上限80文字はDB仕様に合わせた契約案。プロフィール表示名の最終上限もB確認が必要。
- revision GETは下書き/承認済みを問わず最新版。`?version=N`は特定版、存在しない版は404。
- suggestは保存しない`PublicPersonality`応答。提案を採用しても改めてrevisionを保存してから承認する。
- onboarded_atは初回人格承認と同じトランザクションでAPIが設定し、再承認でも初回日時を保持する。UIから書き換えない。
- 任意のアイコンStorageアップロードは本画面に追加していない。

Mock成功は実APIの所有権/暗号化/競合保証の証明ではない。本番APIは未実装のまま。
#15の合意後にpreview契約との差分を解消し、実接続・B/Dレビューを完了してからReadyにする。

ローカルで画面を手動確認する場合（合成データ専用、実キー不要）:

```bash
# ターミナル1: テスト用Auth/APIを起動
pnpm --filter web exec tsx e2e/support/mock-service.ts
# ターミナル2: 127.0.0.1で開く（localhostと混ぜない）
API_URL=http://127.0.0.1:8001 \
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:8001 \
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=synthetic-public-key \
NEXT_PUBLIC_SITE_URL=http://127.0.0.1:3000 pnpm dev:web --hostname 127.0.0.1
```

http://127.0.0.1:3000/login で同意→Google/GitHub→テストOAuthのnormalを選ぶ。
forbidden/inactive/unavailable/failure/cancelで拒否・失敗を再現できる。
テストAuthはセッションごとに状態を分離し、プロセス停止で消える。実ユーザーの情報を入れない。

## #23 ダッシュボード・会話・通知（Draft）

依存: #17（PR #46）、#11（デザイン未提供）、#21（正式API未提供）。
`activity_preview.py`に文書を基にしたPydantic案を分離し、既存preview OpenAPI/型へ生成する。
partner/participantsは`{id,name}`、開始前日時/未生成要約はnull。PR #47のBレビューでこの形に合わせる方針を確認済み。
Discord連携の読み取りだけはダッシュボードのため#26の契約案を先行する。

一覧・通知・ダッシュボードは30秒、queued/runningの詳細は15秒Polling。
完了/失敗/停止後は詳細Pollingを止め、非表示タブでは自動取得を控える。
リクエストの重複を避け、移動・cursor変更時はabort/連番で古い応答を破棄する。
401/403/404等の失敗で表示データを消去し、所有者変更でクライアント表示を作り直す。
429はBFFのRetry-Afterを引き継ぎ、指定時間（未提供時60秒）まで再取得を止める。
ページングは不透明cursor、既読は成功時だけ反映し、失敗時は未読のまま再試行可能。
発言はturn_noのDOM順、生成時刻とAI分身ラベルを表示する。第三者公開は行わない。

テストOAuthの`activity`は承認済みAIと合成会話12件、`activity-empty`は空の交流を返す。
本番FastAPIに業務APIを追加したことや、実認可が完了したことを意味しない。

## #24 交流設定・予算・停止・ブロック（Draft）

依存: #17（PR #46）、#12（デザイン未提供）、#16（正式設定/ブロックAPI未提供）。
今回のブランチは#23の共通取得・会話導線を利用するため、PR #47も先行する。
`interaction_preview.py`のPydantic案からsettings GET/PATCH、blocks PUT/DELETE、
公開先確認用のDiscord guilds GETを生成する。公開先の名称は#26と確認するnullableの案。
名称/ID/有効チャンネルを確認できない場合は公開ONを無効化するが、公開撤回は可能。

頻度0〜2回、非負・有限のUSD予算、Web参加者固定を検証する。予算だけの変更では
交流/DiscordをONにせず、保存結果を確認してから表示を更新する。エラー時の表示は前回の確認値で、
結果不明時は最新状態を再確認する。停止は入力中の不正な予算やLLM情報の欠落と独立して実行できる。

#14のLLM供給元/データ条件が未決定のため、`ALTER_LLM_NAME`と
`ALTER_LLM_DATA_POLICY_URL`（https、ローカル開発だけhttp localhost可）が未設定なら交流ONを拒否する。
デプロイ時は#14のレビュー済み値だけを設定する。LLMへの人格/必要記憶の送信、他者AIとの自動交流、
ブラウザ閉鎖後の継続、会話90日保存、Web参加者閲覧を分けて表示し、未チェックの確認でONにする。
E2Eだけは「合成データ専用LLM・外部送信なし」とローカルの利用説明URLを設定する。

Discord全体公開は別の確認。サーバー/チャンネル名とID、本人のギルド別同意状態、閲覧範囲、
完全回収不可を表示する。全体ONだけでギルド別同意を付与せず、Bot操作を案内する。
公開先変更時の同意失効と双方の同意検査、監査の文面版記録は#16/#26/#27のAPI責務であり、Mockでは保証しない。

ブロック/解除は確認Dialogと成功後の反映を共通部品にする。会話の初期状態取得契約が未確定のため、
会話画面では未確認と表示し、まず冪等なPUTでブロックを確定できる。#25の関係性リストでは
APIのblocked値を使う。未知の状態を「未ブロック」と推測しない。

## #25 記憶・関係性リスト（Draft）

依存: #9（PR #45）、#24（PR #48）、#11（デザイン未提供）、#22（正式API未提供）。
`memory_preview.py`のPydantic案から一覧/削除/関係性を生成する。Memoryの出典IDはDBのnullableに
合わせた案で、欠落・削除済みなら参照不可を案内し、有効な出典リンクから会話へ進める。
会話が404なら本文を表示せず記憶一覧に戻れる。出典会話が消えた場合の派生記憶削除はAPIの責務。

記憶は重要度・作成日・出典とともに表示し、本人の確定事実と断定しない。削除は明示確認後に
即時反映し、503/通信失敗では一覧を戻して再確認・再試行を案内する。認可拒否では本文を消し、
404では削除済みの可能性があるため復元せず一覧を再取得する。再読込後の削除をMockで検査する。
削除中はページ切替/他の削除を止め、Mock cursorも削除によるoffsetずれを起こさない。

関係性は0回=初対面、1〜2回=顔見知り、3回以上=よく交流する相手。共通の公開興味・最終交流日・
回数・APIのblockedを表示し、確認付きブロック/解除を#24の部品で実行する。
人間同士の友人関係/相性を断定せず、グラフは追加しない。両一覧は1列でPC/mobile/320pxを検証する。

正式な所有権/保持期限/出典削除連動/削除記憶の再投入防止は#22のAPI受け入れ待ち。
本番FastAPIの業務APIは未実装。合成データのMock成功だけでIssue完了にしない。

## #31 アカウント・通知・削除（Draft）

依存: #9/#12/#16/#30。#25の共通画面と部品を利用し、Draftのbaseは#25。
表示名（DBに合わせ80文字）、Web通知/任意Discord DM、本人再確認と削除受付・本人だけの進捗を実装。
#30の進捗APIは`GET /users/me/deletion`という未合意のPydantic案。一般業務APIがACCOUNT_INACTIVEを
返す削除要求後も、この経路だけは本人の最小状態を返す契約が必要。Auth削除後は私的窓口を案内する。

再確認は既存Google/GitHubのPKCE OAuth、短命HttpOnly challenge、同一sub確認を使う。
Auth getUserで検証済みJWTのoauth amr時刻が5分以内の場合だけ、現在のaccess tokenと本人に結びつけた
短命HttpOnly UI receiptを発行する。token_refresh/iatだけでは承認しない。BFFも削除直前に再確認する。
5分という窓とamr提供条件は#30へのレビュー案で、満たせない場合は削除要求を止める。
APIは署名・本人・fresh OAuthを独立検査し、UI receiptやconfirmだけを認可根拠にしてはいけない。
[Supabase amr](https://supabase.com/docs/guides/auth/jwt-fields)を2026-11-08確認。

mainのADR-014では招待はDB＋運営CLIへ変更済み。フロントは招待識別情報を保持せず、削除時の
DB招待失効とAI停止/未配信取消は#30のトランザクションに委ねる。本DraftのMockはUI確認用であり、
実際のパージ・暗号化・法務確認を完了したものではない。

## #29 Discord本人連携（Draft）

依存は#9/#12/#26/#27。#31の再確認/共通設定を先行baseとする。
/settings/discordとサーバー専用/auth/discord/callbackを追加。startのURLはhttps://discord.com、
identifyだけのscope、登録したcallback、単一のstate/redirect_uri等に限定する。
短命HttpOnly state hashと同一本人をWebでも確認し、code/stateはサーバーからBearer付きAPIへ送る。
APIは10分・使い捨て・本人拘束stateを独立検査し、OAuth secret/tokenを通常応答へ返さない。

連携と交流/全体公開/ギルド同意/DMは独立。Botの/agent publishと/server setupを案内するだけで
Webからギルド権限や同意を付与しない。解除の確認直前に完全回収不可を説明する。
解除後の未配信取消・全体公開/DM/ギルド同意失効の原子性は#26で受け入れる。
Mockは実Discordへ接続せず、ブラウザE2EもDiscordの認可画面を合成応答へ置換する。

## #35 実環境接続の準備（Draft）

Vercel環境別テンプレート、build時の公開変数/接続先検査と認証なしスモークを追加。
実環境は未設定のためデプロイ・callback登録・実API受け入れは未実施。
依存・登録値・手順は[Webデプロイ](25-web-deployment.md)を参照する。

## #37 MVP受け入れの準備（Draft）

10合成IDのMock操作、375/320px axe/keyboard、live証跡の充足性判定器を追加。
手順と実環境で必要な全caseは[MVP受け入れ](26-mvp-acceptance.md)を参照する。
実環境未設定のため実10人の受け入れ・性能測定・本番公開は未実施。
## PRレビュー反映（#45）

OAuth callbackの入場判定が403 `INVITATION_REQUIRED` / `ACCOUNT_INACTIVE`なら、
このブラウザのSupabaseセッションを終了して元の拒否理由を表示する。503ではセッションを終了せず再試行できる。
BFF/transportの共通エラーがない場合のコードは`SERVICE_UNAVAILABLE`に統一する。

## PRレビュー反映（#46）

人格revision保存には編集元の版を送り、成功後だけ次の期待版に進める。409では入力を保持して明示的な再読込を案内し、自動で期待版を更新しない。
コメントの`base_version`案に対し、PR #58の現在のPydantic正本は必須の`expected_version`なのでpreview/生成型/UI/Mockをその名前に合わせた。
初回作成には期待版を送らず、作成後に取得した版を次の保存の基準にする。
提案応答は版・承認日時を持たず、本人が適用・保存・承認するまで人格版を作らない。

## PRレビュー反映（#47）

`cost_usd`は本人AIの確定費用だけという方針をAPI説明・生成型・一覧/詳細文言に反映した。
集計対象列と未確定時のnull/0は#21で確定待ち。nullは従来どおり未提供として表示し、0へ変換しない。

## PRレビュー反映（#48）

予算は小数6桁まで、1会話上限は日次予算以下として保存/開始の両方で検証する。
交流ONには人格承認に加え`agent.status === active`が必要。停止・公開撤回はこれらの条件と独立したまま。
開始の409は停止状態または同意設定を確認する案内、422は運営上限・小数桁・予算の大小関係を確認する案内を表示する。
運営上限はAPI側設定を正本とし、UIに既定の$1を固定しない。現時点では応答フィールドを追加せず、422で運営への確認を案内する。
Webの`ALTER_LLM_NAME`/`ALTER_LLM_DATA_POLICY_URL`とAPIの`ALTER_CONSENT_VERSION`は#14の承認値を同時に設定する。
