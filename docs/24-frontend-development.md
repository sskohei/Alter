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

依存は#8（PR #44）、#9（PR #45）、#10（Figma待ち）、#15（正式API待ち）。
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
- revision GETが返す最新下書き/承認済み版の選択、競合409の判定と期待版の伝え方。
- suggest応答の永続化有無。フロントは提案を採用しても改めてrevisionを保存してから承認する。
- onboarded_atの更新タイミングはAPI側で確定し、UIから推測して書き換えない。
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

依存: #17（PR #46）、#11（Figma未提供）、#21（正式API未提供）。
`activity_preview.py`に文書を基にしたPydantic案を分離し、既存preview OpenAPI/型へ生成する。
partner/participantsは`{id,name}`、開始前日時/未生成要約はnullの案で、#21でB確認が必要。
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

依存: #17（PR #46）、#12（Figma未提供）、#16（正式設定/ブロックAPI未提供）。
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
