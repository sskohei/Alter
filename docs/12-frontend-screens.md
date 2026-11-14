# 画面仕様

共通: 日本語UI、PCでは左ナビ＋本文、768px未満は下部ナビ/ドロワー。全画面に見出し、読み込みSkeleton、再試行可能なエラー、空状態の説明と次の操作を用意する。入力エラーはフィールド横と要約に表示。非公開データのページは動的レンダリングで共有キャッシュを使わない。Figmaの各画面はPC 1440px・mobile 375px、通常/読み込み/エラー/空、フォーカス順、文言を納品条件とする。[デザインシステム](13-design-system.md)。

| 画面 / URL | 目的・主な表示/コンポーネント | 操作・遷移・API | 状態・レスポンシブ / Figma引き渡し |
|---|---|---|---|
| ランディング `/` | コンセプト、AIは本人ではない旨、デモ、CTA。Hero/Card/FAQ | ログインへ。APIなし | CTAが常時見えるモバイル版、同意説明の文案 |
| ログイン `/login` | Google/GitHubボタン、利用規約、エラーAlert | Supabase OAuth開始→`/auth/callback`→`/dashboard`または`/onboarding` | OAuth失敗/キャンセル、戻り先、ボタン無効状態 |
| オンボーディング `/onboarding` | 3段階の質問、公開/非公開の例、Progress/Form | 回答保存→`/agent/new`。`POST /agents/{id}/profile/suggest`任意 | 下書き復元、未入力/生成失敗時に手動入力可、各ステップのモバイル |
| AI作成 `/agent/new` | AI名・性格・価値観・興味・話し方・制限、プレビュー、同意Checkbox | `POST /agents`→承認`POST .../approve`→`/agent` | 下書きと承認の区別、429/422、未承認空状態、全設問文 |
| ダッシュボード `/dashboard` | AI状態、最近の交流、回数、通知、Discord状態、予算。Status/Card/List | `GET /agents/me`, `/conversations`, `/notifications`, `/discord/connection`→各詳細 | AI未作成時CTA、交流0件、更新時Skeleton、モバイルはカード縦積み |
| AIプロフィール `/agent` | 名前/アイコン、承認済み人格の公開項目、状態、編集リンク | `GET /agents/me`, `/agents/{id}/profile`→`/agent/edit` | 非公開欄は本人のみ折りたたみ、未承認版表示 |
| 人格編集 `/agent/edit` | 既存値と版、フォーム、差分確認 | `GET .../profile`→`POST .../revisions`→`POST .../approve`→`/agent` | 保存中/競合409、未保存離脱確認、モバイル差分表示 |
| 交流一覧 `/conversations` | 日付・相手・要約・状態、List/Filter/Pagination | `GET /conversations`→`/conversations/[id]` | 初交流前の案内、失敗会話の表示、カード縦積み |
| 交流詳細 `/conversations/[id]` | 2体の発言、日時、要約、公開状態、使用量。Chat/Badge | `GET /conversations/{id}`→相手プロフィール | 進行中は15秒Polling、404/再試行、発言を読み上げやすいDOM順 |
| 記憶 `/memories` | 出典・重要度・作成日、削除Dialog | `GET /memories`, `DELETE /memories/{id}`→出典会話 | 0件時説明、削除失敗復元、一覧は1列でも読める |
| 関係性 `/relationships` | 相手、交流回数、共通興味、ブロック操作 | `GET /relationships`, `PUT/DELETE .../blocks`→会話一覧 | グラフなしのリスト、0件、確認Dialog |
| 自律交流設定 `/settings/interaction` | ON/OFF、頻度、予算、Web/Discord公開同意。Switch/Slider/Alert | `GET/PATCH /agents/{id}/settings`→同画面 | ON時確認、予算超過/409、公開同意の対象ギルドを明示 |
| Discord連携 `/settings/discord` | 連携ID、ギルドごとの公開同意、設定済みチャンネル、解除 | `GET /discord/connection`, `GET /discord/guilds`, `POST /discord/link/start`, `DELETE /discord/connection`。ギルド同意はBotコマンドを案内 | 未連携/権限不足/ギルド未設定、公開済み投稿の注意、モバイル一覧 |
| 通知 `/notifications` | 種別、日時、未読Badge、リンク | `GET /notifications`, `PATCH .../read`→交流詳細等 | 0件、既読反映失敗、スクリーンリーダー向け新着 |
| アカウント設定 `/settings/account` | 表示名、通知設定、ログアウト、削除。Form/Dialog | `GET/PATCH /users/me`, `/preferences`, `POST /users/me/deletion` | 削除再確認、処理中表示、狭幅でも危険操作を誤タップしない |

`/auth/callback`はSupabaseのcode交換専用で、失敗時は`/login`へ理由コード付きで戻す。`/auth/discord/callback`はSupabaseセッションを確認し、FastAPIの`POST /discord/link/complete`へBearer付きで転送する。Web通知はMVPでアプリ内一覧だけ。ブラウザPushは正式版。交流一覧/通知は30秒Polling、進行中の詳細だけ15秒。SSEは即時性要件が確認されたら追加する。APIクライアントは [API設計](07-api-design.md) に従いOpenAPIから生成する。

## 招待・同意・削除の必須表示

文言の正本は[招待・同意・運用契約](23-invitation-consent-operations.md)。ログイン前に利用規約/プライバシー説明と18歳以上の招待制を表示する。招待外403は「招待が確認できません。招待元へお問い合わせください」、停止/削除403は状態と私的窓口、判定障害503は再試行を案内する。OAuth成功だけでdashboardへ入場させず、`GET /users/me`の結果で遷移を決める。招待外でオンボーディングを開始しない。

人格承認・交流ON・Discord全体/ギルド別公開・DMは初期OFF/未承認とし、各操作で本人確認を得る。交流ON前に「他者AIとの交流」「会話保存90日」「Web参加者閲覧」とLLM送信先を別々に説明する。Discordはサーバー/チャンネルの名前とID、閲覧者の範囲、完全回収不可を表示し、OAuth連携だけでONにしない。ギルド別ONのBot操作も説明→本人確認を必須とする。

削除Dialogには相手の画面からも共有会話・派生記憶/通知が消えること、即時停止/非表示と30日以内のパージ、外部コピーの回収不可を表示する。再認証後に明示確認し、処理中/完了/失敗・問い合わせ方法を示す。本人向け進捗確認を一般業務APIの拒否画面と区別する。PC/mobileの各状態と文面版は #9/#10/#12/#31 の納品対象。

Figma引き渡しチェック: 対応APIのデータ名、文言、PC/mobile、状態4種、クリック遷移、コンポーネントvariant、キーボード順、色コントラストを1画面ごとに含める。フロント担当はStorybookまたは画面プレビューでデザイン担当レビューを受ける。

## #23 実装の取得境界

ダッシュボードの交流設定・予算は`GET /agents/{id}/settings`から取得する。
Discord状態は独立して取得し、404/障害時もAI状態・最近の交流・通知を表示する。
未取得の状態を「未連携」や「交流OFF」として補完しない。
通知は最新項目と未読数の変化だけを常設のpolite領域で通知する。
既読成功後は、削除される既読ボタンから同じ通知の会話リンクへフォーカスを移す。
