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
## PRレビュー反映（#45）

OAuth callbackの入場判定が403 `INVITATION_REQUIRED` / `ACCOUNT_INACTIVE`なら、
このブラウザのSupabaseセッションを終了して元の拒否理由を表示する。503ではセッションを終了せず再試行できる。
BFF/transportの共通エラーがない場合のコードは`SERVICE_UNAVAILABLE`に統一する。

## PRレビュー反映（#46）

人格revision保存には編集元の版を送り、成功後だけ次の期待版に進める。409では入力を保持して明示的な再読込を案内し、自動で期待版を更新しない。
コメントの`base_version`案に対し、PR #58の現在のPydantic正本は必須の`expected_version`なのでpreview/生成型/UI/Mockをその名前に合わせた。
初回作成には期待版を送らず、作成後に取得した版を次の保存の基準にする。
提案応答は版・承認日時を持たず、本人が適用・保存・承認するまで人格版を作らない。
