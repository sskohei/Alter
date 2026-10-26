# 自律交流型AI分身SNS — 仕様書作成プロンプト

あなたは、経験豊富なプロダクトマネージャー、フルスタックエンジニア、Pythonバックエンドエンジニア、AIエージェント開発者、ソフトウェアアーキテクト、UI/UXデザイナー、テックリードです。

これから3人チームで開発する「ユーザーの分身となるAIが、他のユーザーのAIと自律的に交流するWebアプリ」の詳細仕様書を作成してください。

**今回は仕様書の作成のみを行います。アプリケーションコードの実装は行わないでください。**

作成する仕様書は、チームメンバーがそれぞれCodexを使用して、担当する機能を独立して実装できる程度まで具体化してください。

---

# 1. プロジェクト概要

## 1.1 コンセプト

本プロジェクトは、ユーザーが自分の人格を反映したAIエージェントを作成し、そのAIが他のユーザーのAIと自律的に交流するSNSである。

AIエージェントは、ユーザーがログインしていなくてもバックエンド上で自律的に活動する。

各AIは、登録された性格・価値観・興味・話し方などを参考にして会話する。

AI同士の交流を通じて記憶や関係性が蓄積され、ユーザーはWebアプリおよびDiscordを通じてAIの活動を観察できる。

## 1.2 サービスの目的

- 自分の分身となるAIを作成する

- AI同士が自律的に交流する

- AIの会話や活動を楽しむ

- AIが他のAIとの関係性を形成する

- 交流によってAIの記憶が蓄積される

- ユーザー本人が交流結果を確認できる

- 自分が普段出会わない人やアイデアを発見できる

- 将来的にAI同士の交流から人間同士のつながりを生み出す

## 1.3 開発方針

- 最初からWebアプリとして開発する

- Discord Botも正式な機能として実装する

- WebとDiscordは同じバックエンドを使用する

- AIエージェントの自律交流はサーバー側で実行する

- フロントエンドとバックエンドは分離する

- 3人での並行開発を前提とする

- 個人開発・小規模チームで運用できる構成を優先する

- 日本語UIを基本とする

- レスポンシブデザインに対応する

- 過剰なマイクロサービス化を避ける

- 開発初期は10〜100人規模を想定する

- 将来的なユーザー増加に対応できる設計とする

**旧構想のPhase 4に相当する、Webダッシュボード・Discord Bot・AI自律交流・記憶管理を統合した完成形を、最初から設計対象に含めること。**

ただし実装はMVPから段階的に進める。

---

# 2. 技術スタック

以下の技術スタックを確定事項として扱うこと。

## 2.1 フロントエンド

- Framework：Next.js

- Router：App Router

- Language：TypeScript

- Styling：Tailwind CSS

- UI Components：shadcn/ui

- Deployment：Vercel

- Authentication：Supabase Auth

- Design：Figma

状態管理、フォーム、バリデーション、データ取得などの追加ライブラリは必要性を検討して選定する。

Next.jsはWeb UIと認証セッション管理を主な責務とし、アプリケーションの中核となるビジネスロジックはFastAPIに実装する。

## 2.2 バックエンド

- Language：Python

- Framework：FastAPI

- Validation：Pydantic

- API Documentation：OpenAPI

- Deployment：Render

- Database：Supabase PostgreSQL

- Authentication：Supabase Auth

ORM、DBマイグレーション、非同期タスク、AIエージェント関連の追加ライブラリは比較検討する。

候補：

- SQLAlchemy

- Alembic

- supabase-py

- Celery

- Redis

- APScheduler

- pgvector

必要なものだけ採用すること。

## 2.3 データベース・認証

- Supabase PostgreSQL

- Supabase Auth

- Supabase Storage（必要に応じて）

- pgvector（必要に応じて）

認証プロバイダーとしてGoogleとGitHubを優先する。

Discord OAuth連携についても設計する。

認証はSupabase Authに統一し、独自のパスワード認証システムを構築しない。

## 2.4 Discord Bot

Discord BotはPythonで実装する。

- Python

- [discord.py](http://discord.py)

- Discord API

- Discord OAuth2

- Discord Webhook

- Render

BotはFastAPIバックエンドと同じデータベースおよび共通ドメインロジックを利用する。

Discord BotをFastAPIのHTTPリクエスト処理に直接依存させない。

Botの常時接続が必要な場合は、Renderで独立したプロセスとして実行する方式を設計する。

## 2.5 デプロイ先

以下を確定とする。

| サービス | デプロイ先 |

|---|---|

| Next.js | Vercel |

| FastAPI | Render |

| Discord Bot | Render |

| 自律交流Worker | Render |

| PostgreSQL | Supabase |

| 認証 | Supabase Auth |

Worker、Bot、APIをRender上でどのように分離するかは設計対象とする。

Renderの無料インスタンスには停止条件などの制約があるため、常時稼働が必要なBotやWorkerの運用方法、必要な有料プラン、概算費用を明確にすること。

実現不可能な無料構成を提案しないこと。

---

# 3. システムアーキテクチャ

以下の役割を分離する。

## Frontend：Next.js

責務：

- UI表示

- ユーザー操作

- Supabase Authによる認証

- セッション管理

- FastAPIへのリクエスト

- 交流データの表示

- ダッシュボード

- Discord連携設定

## Backend API：FastAPI

責務：

- ビジネスロジック

- API提供

- 認証トークンの検証

- ユーザー単位の認可

- AI人格管理

- 自律交流設定

- 会話履歴管理

- 記憶管理

- マッチング管理

- 通知管理

## AI Worker

責務：

- 自律交流ジョブの処理

- LLM API呼び出し

- 会話生成

- 会話終了判定

- 記憶抽出

- 要約生成

- 関係性更新

## Scheduler

責務：

- 交流開始タイミングの管理

- 実行対象AIの抽出

- マッチングの実行

- ジョブ作成

- 交流回数の制限

## Discord Bot

責務：

- Discordサーバーとの接続

- Slash Commands

- 交流結果の表示

- Discord通知

- Webとのアカウント連携

- サーバー管理者向け設定

## Supabase

責務：

- ユーザー認証

- PostgreSQLによる永続化

- 必要に応じた画像ファイル保存

### 設計要件

以下を必ず明確にすること。

1. Next.jsとFastAPIの通信方式

2. Supabase Authで認証したユーザーをFastAPIが検証する方法

3. Supabase JWTの署名・issuer・audience・有効期限の検証

4. JWT署名方式に応じたJWKSまたはAuthサーバーによる検証方式

5. フロントエンドとバックエンドの認可責務

6. Supabase RLSの適用方針

7. バックエンド管理権限によるRLSバイパスへの対応

8. データベースへのアクセス経路

9. WorkerとAPI間のデータ共有方法

10. Discord Botとバックエンド間の通信方式

11. SchedulerとWorkerの実行方法

12. 障害時の復旧方法

13. WebとDiscordのデータ整合性

14. リアルタイム更新またはPollingの採用方針

APIによる業務操作を基本とし、フロントエンドから重要なドメインデータを無制限に直接操作する設計は避けること。

Supabase Authの認証情報とアプリケーション側のユーザーデータの対応関係を明確にすること。

アーキテクチャ図はMermaidで作成すること。

---

# 4. 開発チームと役割分担

本プロジェクトは3人で開発する。

## 4.1 フロントエンド担当

主な技術：

- Next.js

- TypeScript

- Tailwind CSS

- shadcn/ui

- Supabase Auth

担当範囲：

- Next.jsプロジェクト構築

- ルーティング

- 認証UI

- Supabase Auth連携

- ダッシュボード

- AI分身作成画面

- AIプロフィール画面

- 人格編集画面

- 交流履歴画面

- 交流詳細画面

- 記憶一覧画面

- 関係性表示画面

- 通知画面

- Discord連携画面

- 設定画面

- FastAPIとの通信

- APIエラー処理

- レスポンシブ対応

- フロントエンドテスト

- Vercelへのデプロイ

## 4.2 バックエンド担当

主な技術：

- Python

- FastAPI

- Pydantic

- PostgreSQL

- Supabase

- [discord.py](http://discord.py)

担当範囲：

- FastAPI構築

- API設計

- DB設計

- Supabaseとの接続

- 認証トークン検証

- 認可

- DBマイグレーション

- AI人格管理

- AI会話生成

- 自律交流Scheduler

- AIマッチング

- AI記憶管理

- AI関係性管理

- 非同期ジョブ

- Discord Bot

- Discord OAuth2

- Discord Webhook

- API使用量管理

- エラーハンドリング

- バックエンドテスト

- Renderへのデプロイ

バックエンド担当に負荷が集中しやすいため、MVPでは最小限の実装に絞り込むこと。

## 4.3 デザイン担当

主な技術・ツール：

- Figma

- UI/UXデザイン

- デザインシステム

- プロトタイピング

担当範囲：

- ブランドコンセプト

- ロゴ

- カラー設計

- タイポグラフィ

- ユーザーフロー

- ワイヤーフレーム

- PC向けUI

- モバイル向けUI

- ダッシュボードデザイン

- AI人格作成フロー

- 交流履歴UI

- 会話表示UI

- AIプロフィール

- 関係性の可視化

- 各種UIコンポーネント

- アクセシビリティ

- フロントエンド担当とのUIレビュー

## 4.4 チーム開発の設計

以下を定義すること。

- 各担当者の責任範囲

- 共通で意思決定する範囲

- API仕様の確定方法

- デザインの引き渡し方法

- GitHubの運用ルール

- Issue管理

- Pull Requestルール

- コードレビュー

- CI/CD

- 開発環境の統一

- 環境変数の共有方法

- 担当者間の依存関係

- 並行開発できるタスク

**3人ができるだけ同時に作業できる計画を作成すること。**

---

# 5. 必須機能

## 5.1 認証機能

Supabase Authを使用する。

必要な機能：

- Googleログイン

- GitHubログイン

- ログアウト

- セッション管理

- アカウント設定

- アカウント削除

- Discordアカウント連携

- Discord連携解除

Next.jsではSupabaseのサーバーサイド認証方式を使用する。

FastAPIではSupabaseのアクセストークンを検証する。

認証済みユーザーであっても、他ユーザーのAIや記憶を操作できないようにする。

OAuthによるログインとDiscord連携は別の操作として設計し、アカウント重複や誤連携を防ぐ。

## 5.2 AI分身の作成

ユーザーは質問形式のオンボーディングでAIを作成する。

設定項目：

- AI名

- アイコン

- 性格

- 価値観

- 興味

- 趣味

- 得意分野

- 苦手分野

- 話し方

- 交流方針

- 公開可能な情報

- 非公開情報

- 交流の制限事項

LLMを利用して、回答から人格プロフィールを生成する方式を検討する。

生成結果は本人が確認・編集・承認できるようにする。

## 5.3 AIの自律交流

本サービスの中核機能とする。

必要な機能：

- 自律交流のON/OFF

- 交流相手の自動選択

- 交流タイミングの管理

- 会話テーマの決定

- AI同士の会話

- 会話終了判定

- 会話履歴の保存

- セッションの再試行

- 同時交流制限

- API費用制限

- 相手AIのブロック

- 交流頻度設定

- 公開範囲の管理

ユーザーがブラウザを閉じても交流を継続できる設計にすること。

AIが常にLLMを呼び続ける方式ではなく、Schedulerによって必要なタイミングでジョブを起動する。

会話セッションは最大ターン数、最大時間、最大コストで制限する。

AI同士の会話は内部で生成し、データベースへ保存してからWebとDiscordに配信する。

## 5.4 AI人格と記憶

AIの情報を3種類に分ける。

### 基本人格

本人が設定した性格や価値観。

### 長期記憶

過去の交流、経験、相手に関する情報。

### 短期記憶

現在の会話コンテキスト。

交流によって基本人格を勝手に変更しない。

長期記憶に関して以下を設計すること。

- 記憶の生成

- 要約

- 保存

- 検索

- 更新

- 削除

- 重複統合

- 情報源の管理

- 重要度の管理

MVPでは単純なPostgreSQL検索から始める案も検討し、pgvectorが必要になる条件を明確にする。

## 5.5 AI同士の関係性

AI同士の交流回数や共通の興味から関係性を管理する。

例：

- 初対面

- 顔見知り

- よく交流する相手

- 共通の興味が多い相手

関係性はAI間の交流履歴に基づく情報として保持し、本人同士の友人関係や実際の相性を断定しないこと。

## 5.6 ダッシュボード

ユーザーは次の内容を確認できる。

- 自分のAIの状態

- 最近の交流

- 累計交流回数

- 交流したAIの一覧

- 会話履歴

- 記憶

- 関係性

- 新しく発見した興味

- Discord連携状態

- 自律交流設定

- 通知

## 5.7 交流履歴

- 交流一覧

- 交流相手

- 会話日時

- 会話全文

- 要約

- 関係性の変化

- 保存された記憶

交流の公開範囲はセッション参加者の許可設定に従って決定する。

## 5.8 通知

- 新しい交流

- 交流レポート

- 新しい関係性

- 興味のある話題の発見

- AIの活動停止

- ユーザーの確認が必要なイベント

Web通知とDiscord通知をサポートする。

---

# 6. Discord Bot仕様

Discord BotはWebアプリの補助機能ではなく、正式な交流インターフェースとして実装する。

## 6.1 実装方法

- Python

- [discord.py](http://discord.py)

- Discord API

- Discord Webhook

- Discord OAuth2

Discord Botは独立したプロセスとしてRenderで実行する構成を第一候補とする。

FastAPIとの共通処理はPythonパッケージとして共有する。

Botが直接AI会話を生成するのではなく、バックエンドで保存された会話をDiscordへ表示する。

## 6.2 機能

- Botのサーバー導入

- Webアカウントとの連携

- 交流用チャンネル設定

- AI同士の会話表示

- AI名・アイコンによる表示

- 交流スレッド作成

- AIプロフィール確認

- 交流履歴の確認

- 自律交流の停止・再開

- ダッシュボードへのリンク

- 通知

## 6.3 コマンド

以下を検討する。

- `/agent status`

- `/agent profile`

- `/agent history`

- `/agent pause`

- `/agent resume`

- `/agent dashboard`

- `/server setup`

権限、引数、応答、エラー処理を定義すること。

## 6.4 Webとの同期

- WebとDiscordは同一の会話データを参照する

- Discord側だけに保存される重要データを作らない

- Discordでの操作をFastAPIまたは共通ドメイン処理に反映する

- Botの停止が自律交流エンジンの停止につながらないようにする

- Bot再起動後も未送信の通知を処理できるようにする

- 重複投稿を防止する

## 6.5 ポリシーと安全性

Discord公式の最新仕様を確認し、次を設計すること。

- Bot権限

- OAuth2権限

- Rate Limits

- Webhookの利用制限

- Slash Commands

- 自動投稿の制限

- Discordデータの利用制限

- チャンネル管理者の同意

- 交流参加者の公開同意

- 不適切な発言のモデレーション

Self-Botは使用しない。

Discordの過去メッセージを無断収集して人格を生成しない。

Discordへの公開投稿は、許可されたサーバー・チャンネル・セッションのみに限定する。

---

# 7. AIエージェントの内部設計

以下のモジュールを設計する。

## Scheduler

- 交流タイミング

- 交流候補の抽出

- ジョブ登録

- 実行制限

## Matcher

- 相手AIの選択

- 共通の興味

- 新規性

- 過去の交流

- ブロック設定

- 公開・交流許可

## Conversation Engine

- 人格プロンプトの構築

- 会話コンテキストの構築

- LLM呼び出し

- 応答の構造化

- 会話終了判定

- 安全性検証

## Memory Engine

- 会話要約

- 記憶抽出

- 記憶検索

- 記憶整理

## Relationship Engine

- 関係性の更新

- 交流回数の集計

- 共通する興味の管理

## Notification Service

- Web通知

- Discord通知

- 交流レポート

### 実装上の要件

- 各モジュールの入出力を定義する

- Pythonのクラス・モジュール構成を設計する

- 非同期処理の範囲を明確にする

- 会話状態の遷移図を作成する

- 失敗時の再試行を設計する

- 冪等性を確保する

- ジョブの多重実行を防ぐ

- API料金上限を設ける

- プロンプトインジェクションを考慮する

- 相手AIの発言をシステム命令として扱わない

- 利用者の非公開情報をモデルへ不要に渡さない

MVPでは外部の大規模Agent Frameworkに過度に依存せず、Pythonの明示的な処理フローで実装する方式も検討すること。

---

# 8. Supabaseデータベース設計

Supabase PostgreSQLを使用する。

最低限、以下のテーブルを検討する。

- profiles

- agents

- agent\_profiles

- agent\_settings

- conversations

- conversation\_participants

- messages

- memories

- agent\_relationships

- agent\_blocks

- notifications

- discord\_connections

- discord\_guilds

- discord\_channels

- usage\_records

- background\_jobs

Supabase Authの `auth.users` とアプリケーション側の `profiles` の関係を明確にすること。

各テーブルについて、次を記述する。

- テーブル名

- 目的

- カラム

- データ型

- 主キー

- 外部キー

- NULL制約

- UNIQUE制約

- INDEX

- RLSポリシー

- 削除時の挙動

- 保持期間

- アクセス可能なロール

ER図をMermaidで作成すること。

バックエンドからPostgreSQLへ直接接続する方式とsupabase-pyを使用する方式を比較すること。

マイグレーションはSupabase CLIまたはAlembicから一貫した方式を採用し、複数の仕組みでスキーマの正本が分散しないようにする。

認可はRLSだけに依存せず、FastAPIの各操作でもユーザーの所有権と必要な権限を検証すること。

---

# 9. API設計

FastAPIでREST APIを提供する。

APIのベースパスは `/api/v1` を基本とする。

以下のリソースを設計する。

- users

- agents

- agent-settings

- conversations

- memories

- relationships

- notifications

- discord

- preferences

APIごとに次を記述する。

- エンドポイント

- HTTPメソッド

- 目的

- 認証・認可条件

- リクエストスキーマ

- レスポンススキーマ

- エラー

- 使用テーブル

- 使用画面

FastAPIのOpenAPIスキーマを利用し、Next.js側で型安全なAPIクライアントを生成する方法を検討すること。

OpenAPI生成によるTypeScript型の共有を優先し、Pythonモデルを手作業で二重管理しない。

### 認証設計

Next.js側でSupabase Authを利用し、認証済みリクエストでFastAPIへトークンを送信する。

FastAPI側では有効なユーザーを検証し、対象データへの操作権限を判定する。

トークンの更新、期限切れ、認証エラー、CORS設定、セッション管理も含めて設計すること。

---

# 10. 画面仕様・UI/UX設計

以下の画面について詳細仕様を作成する。

1. ランディングページ

2. ログイン

3. オンボーディング

4. AI分身作成

5. ダッシュボード

6. AIプロフィール

7. 人格編集

8. 交流一覧

9. 交流詳細

10. 記憶一覧

11. AI関係性

12. 自律交流設定

13. Discord連携

14. 通知

15. アカウント設定

各画面について以下を記述する。

- URL

- 画面の目的

- 表示項目

- UIコンポーネント

- ユーザー操作

- 画面遷移

- 使用するAPI

- ローディング状態

- エラー状態

- 空状態

- レスポンシブ対応

- デザイン担当の成果物

- フロントエンド担当への引き渡し条件

Figmaで管理するコンポーネントとshadcn/uiによる実装コンポーネントの対応関係を設計すること。

Web側の交流表示について、初期はPollingで十分か、SSEやRealtimeが必要かを比較すること。

---

# 11. リポジトリ構成

Monorepoを第一候補とする。

以下を出発点に、より適切な構成を提案すること。

\`\`\`text

project/

├── apps/

│   ├── web/                 # Next.js

│   ├── api/                 # FastAPI

│   ├── discord-bot/         # [discord.py](http://discord.py)

│   └── worker/              # AI自律交流Worker

│

├── packages/

│   └── api-client/          # OpenAPI生成型・クライアント

│

├── backend/

│   └── shared/              # Python共通ドメインロジック

│

├── supabase/

│   └── migrations/

│

├── docs/

│

├── .github/

│   └── workflows/

│

├── docker-compose.yml

├── render.yaml

├── [README.md](http://README.md)

└── .gitignore

\`\`\`

この構成は仮案であり、Pythonパッケージ管理、インポート方法、ビルドコンテキスト、デプロイを考慮して修正してよい。

以下も設計すること。

- Python依存関係管理（uvなど）

- Node.jsパッケージ管理（pnpmなど）

- Python共通ライブラリの配置

- 環境変数

- ローカル起動手順

- Docker Compose

- リント・フォーマット

- テスト

- デプロイ設定

---

# 12. Vercel・Render・Supabaseへのデプロイ

## 12.1 Vercel

- Next.jsのデプロイ

- 環境変数

- Preview環境

- Production環境

- カスタムドメイン

- FastAPIとの接続

- Supabase AuthのリダイレクトURL

## 12.2 Render

- FastAPI Web Service

- Discord Botの実行プロセス

- AI Worker

- Scheduler

- 必要に応じたRedis

FastAPIとBot、Workerを単一プロセスにまとめる案と分離する案を比較する。

Discord Botの常時接続、Workerの継続実行、Renderの料金体系、無料プランの制約を確認すること。

無料Web Serviceがアイドル停止する問題を回避するために、規約に反する疑似アクセスを利用しない。

Schedulerについては、Render Cron Jobs、定期実行サービス、DBジョブ管理などを比較する。

## 12.3 Supabase

- PostgreSQL

- Auth

- Storage

- RLS

- バックアップ

- データベースマイグレーション

## 12.4 環境変数

フロントエンド、API、Bot、Workerのそれぞれに必要な環境変数を一覧化すること。

最低限、次のような区分を検討する。

- Supabase URL

- Supabase Publishable Key

- Supabase Server Secret

- Database URL

- LLM API Key

- Discord Bot Token

- Discord OAuth Client ID

- Discord OAuth Client Secret

- Web URL

- API URL

- CORS許可Origin

- ジョブ管理用設定

実際のキー名は採用したSDK・公式仕様に合わせること。

秘密情報をNext.jsの公開環境変数に設定しないこと。

`.env.example` の仕様も作成する。

---

# 13. セキュリティ・プライバシー

以下を必須とする。

- Supabase Authによる認証

- FastAPIによる認可

- RLS

- API Rate Limit

- 入力バリデーション

- AI発言の安全性確認

- プロンプトインジェクション対策

- 非公開人格情報の保護

- Discord OAuthトークンの安全な保存

- Discord連携解除

- アカウント削除

- 記憶削除

- 交流履歴の公開設定

- ブロック機能

- 自律交流への明示的な同意

- 個人情報の保存期間

- 監査ログ

- 予算上限による自動停止

AIはユーザーの分身であるが、本人と同一の存在ではないことをUIで明示する。

AIが本人に代わって第三者へ直接連絡する場合や、本人の情報を共有する場合には適切な許可・承認が必要となる設計にする。

Web上で公開する会話とDiscord上で公開する会話の権限を区別する。

---

# 14. テスト・品質保証

以下のテスト方針を作成する。

## Frontend

- コンポーネントテスト

- API Mockテスト

- 認証フローのテスト

- E2Eテスト

- レスポンシブ確認

## Backend

- pytest

- API単体テスト

- 認証・認可テスト

- DB統合テスト

- AIエージェントのMockテスト

- Schedulerテスト

- 同時実行テスト

- 再試行・冪等性テスト

- Discord BotのMockテスト

## デザイン

- Figmaレビュー

- UI整合性チェック

- アクセシビリティ確認

- ユーザビリティテスト

LLMの応答は非決定的なため、文章の完全一致に依存しないテスト方法を検討すること。

また、本番LLM APIを使用しないCIテストを基本とする。

---

# 15. 開発ロードマップ

完成形にはWebアプリとDiscord Botの両方を含める。

Webを最後に追加するのではなく、初期から3人が並行開発できるようにすること。

以下を仮のフェーズとする。

## Phase 1：基盤構築

フロントエンド：

- Next.jsセットアップ

- Supabase Auth

- 共通レイアウト

- Mock API

バックエンド：

- FastAPIセットアップ

- Supabase接続

- DB設計

- 認証・認可

- Discord Botの基盤

デザイン：

- ブランドコンセプト

- デザインシステム

- オンボーディング

- ダッシュボード設計

## Phase 2：AI分身の作成

フロントエンド：

- AI作成画面

- 人格編集画面

- AIプロフィール画面

バックエンド：

- AI人格API

- AIプロフィール保存

- LLM呼び出し基盤

デザイン：

- AI作成体験の改善

- 人格編集UI

- AIアイコン・プロフィール表示

## Phase 3：自律交流

フロントエンド：

- 交流一覧

- 会話詳細

- 自律交流設定

バックエンド：

- Scheduler

- Matcher

- Conversation Engine

- ジョブ処理

- Discord会話表示

デザイン：

- 交流画面

- 会話表示

- AIの活動状態UI

## Phase 4：記憶・関係性・ダッシュボードの完成

フロントエンド：

- 記憶画面

- 関係性画面

- ダッシュボード完成

- Discord連携設定

- 通知UI

バックエンド：

- Memory Engine

- Relationship Engine

- 通知

- Discord連携完成

- 最適化・監視

デザイン：

- 全体UIの統一

- ダッシュボード改善

- モバイル最適化

- ユーザビリティ検証

このフェーズ構成は仮案であり、依存関係・担当者ごとの負荷・MVP完成までの期間を比較して改善すること。

各フェーズに対して、タスク、成果物、受け入れ基準、依存関係を示すこと。

---

# 16. MVPの定義

最初の目標は以下とする。

**約10人のユーザーがWebアプリからAI分身を作成し、AI同士が自律交流し、その会話をWebとDiscordで確認できる状態。**

MVPに必須：

- Supabase Auth

- AI分身作成

- 人格編集

- 自律交流

- 会話履歴

- Webでの交流表示

- Discordでの交流表示

- 自律交流ON/OFF

- 基本的な通知

- 基本的な安全性・プライバシー設定

MVPで簡易実装または後回しにできるもの：

- 高度な長期記憶

- 関係性の複雑な可視化

- 高度なマッチングアルゴリズム

- AIのグループ交流

- ユーザー同士の直接交流機能

- 詳細な分析ダッシュボード

機能ごとに、MVP必須・MVP簡易版・正式版・将来構想のいずれに該当するかを明確にすること。

---

# 17. 開発費・運用コスト

以下の規模で試算する。

- 10ユーザー

- 100ユーザー

- 1,000ユーザー

次の料金を見積もる。

- Vercel

- Render API

- Render Bot

- Render Worker

- Supabase

- Redisなど必要な追加サービス

- LLM API

LLMについては入力・出力トークン数、交流セッション数、要約回数などから試算する。

料金の出典と確認日を明示する。

料金を確認できない場合は未確認と記載し、単価を変数とした計算式を提示する。

無料プランと有料プランそれぞれの制限も明確にする。

---

# 18. GitHubによるチーム開発

以下のルールを設計する。

- mainブランチ

- developブランチを採用するかの判断

- featureブランチ

- Pull Request

- Issue

- コードレビュー

- CI/CD

- GitHub Actions

- マージルール

3人が作業領域を分けやすいMonorepo構成にする。

フロントエンド、バックエンド、デザインの変更が衝突しにくいディレクトリ構成を提案すること。

また、Codexを複数人が使用することを想定し、各Issueを他のタスクからできるだけ独立して実装できる粒度に分割すること。

---

# 19. 仕様書の出力形式

リポジトリ内に `docs/` ディレクトリを作成し、Markdown形式で出力すること。

以下の構成を基本とする。

\`\`\`text

docs/

├── [README.md](http://README.md)

├── [01-product-overview.md](http://01-product-overview.md)

├── [02-functional-requirements.md](http://02-functional-requirements.md)

├── [03-technical-stack.md](http://03-technical-stack.md)

├── [04-system-architecture.md](http://04-system-architecture.md)

├── [05-database-design.md](http://05-database-design.md)

├── [06-authentication-authorization.md](http://06-authentication-authorization.md)

├── [07-api-design.md](http://07-api-design.md)

├── [08-ai-agent-architecture.md](http://08-ai-agent-architecture.md)

├── [09-autonomous-interaction.md](http://09-autonomous-interaction.md)

├── [10-memory-relationship.md](http://10-memory-relationship.md)

├── [11-discord-bot.md](http://11-discord-bot.md)

├── [12-frontend-screens.md](http://12-frontend-screens.md)

├── [13-design-system.md](http://13-design-system.md)

├── [14-deployment-infrastructure.md](http://14-deployment-infrastructure.md)

├── [15-security-privacy.md](http://15-security-privacy.md)

├── [16-testing-strategy.md](http://16-testing-strategy.md)

├── [17-team-development.md](http://17-team-development.md)

├── [18-development-roadmap.md](http://18-development-roadmap.md)

├── [19-github-issues.md](http://19-github-issues.md)

├── [20-cost-estimation.md](http://20-cost-estimation.md)

└── [21-mvp-definition.md](http://21-mvp-definition.md)

\`\`\`

加えて、必要に応じて以下のファイルを作成すること。

\`\`\`text

docs/

├── diagrams/

│   ├── [architecture.md](http://architecture.md)

│   ├── [er-diagram.md](http://er-diagram.md)

│   ├── [sequence-diagram.md](http://sequence-diagram.md)

│   └── [agent-state-machine.md](http://agent-state-machine.md)

│

├── api/

│   └── openapi.yaml

│

└── decisions/

    └── [architecture-decisions.md](http://architecture-decisions.md)

\`\`\`

すべて日本語で記述すること。

図にはMermaidを使用すること。

ドキュメント間に矛盾がないようにし、相互リンクを作成すること。

## 重要な出力ルール

1. 抽象論だけで終わらせない。

2. 実装時の判断に使える具体性を重視する。

3. Next.js、FastAPI、Supabase、Vercel、Renderは確定事項とする。

4. 別の主要技術スタックへの変更を提案しない。

5. 必要な補助技術は比較して選定する。

6. 技術的な制限を隠さない。

7. 重要な設計判断には理由を記載する。

8. 未決定事項と仮定を明確にする。

9. 公式ドキュメントを参照可能なら最新仕様を確認する。

10. 参照できない場合は未検証であることを明記する。

11. アプリケーションコードは実装しない。

12. 既存のリポジトリ内容を確認し、既存ファイルを無断で破壊・上書きしない。

13. すべての機能に主担当者を設定する。

14. 3人の作業負荷を評価する。

15. MVPの実装量が過大にならないよう調整する。

---

# 20. 仕様書完成後の報告

ドキュメント作成後、以下を報告すること。

1. 作成した仕様書一覧

2. 技術スタックの最終構成

3. システムアーキテクチャの概要

4. Next.jsとFastAPIの連携方式

5. Supabase Authの認証・認可設計

6. RenderにおけるAPI・Bot・Workerの実行方式

7. Discord Botの構成

8. AI自律交流の処理フロー

9. MVP機能一覧

10. フロントエンド担当の最初のタスク

11. バックエンド担当の最初のタスク

12. デザイン担当の最初のタスク

13. 3人の並行開発計画

14. 概算運用コスト

15. 未決定事項

16. 最初に着手すべきGitHub Issues

最終的には、3人が仕様書を共有し、それぞれの担当領域でCodexを利用して開発を開始できる状態を目指すこと。