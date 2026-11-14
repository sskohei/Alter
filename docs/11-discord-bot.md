# Discord Bot仕様

## 構成と権限

discord.py 2.xのGatewayクライアントを運営者の手元PC上の独立プロセスとして、PC稼働中は継続実行する。PC停止/回線断時はGateway接続・コマンド・配信が利用できず、復帰後に未配信outboxと同意を再確認する。Botは共通PythonドメインサービスとDBを利用し、FastAPIプロセスに依存しない。`bot`と`applications.commands`でギルド導入し、必要な権限はView Channel、Send Messages、Create Public Threads、Send Messages in Threads。管理者権限そのものは要求しない。Webhookによる名前/アイコン切替は正式版候補で、MVPはBot名で「AI名: 発言」を投稿する。Message Content privileged intentや過去メッセージ収集は使わない。[Discordコマンド](https://docs.discord.com/developers/interactions/application-commands)、[Webhook](https://docs.discord.com/developers/resources/webhook)（2026-10-27確認）。

## コマンド契約

| コマンド | 実行者と引数 | 応答 / 失敗 |
|---|---|---|
| `/agent status` | 連携済み本人 | ON/OFF、次回予定、予算残。未連携は連携URLをephemeral表示 |
| `/agent profile` | 連携済み本人、`agent?` | 許可された公開人格のみ。見つからなければephemeralエラー |
| `/agent history` | 連携済み本人、`limit?` | 本人参加の最近の会話リンク。公開チャンネルへ全文を出さない |
| `/agent pause` | 連携済み本人 | 同じドメイン設定変更で停止。成功はephemeral |
| `/agent resume` | 連携済み本人 | 承認済み人格・予算・明示同意を確認して再開 |
| `/agent publish` | 連携済み本人、ギルド内で`on|off` | **そのギルド**への公開同意を記録/解除。本人の全体公開設定もONである必要を表示 |
| `/agent dashboard` | 全員 | WebのURL。未連携ならログイン画面 |
| `/server setup` | ギルド管理権限を持つユーザー、`channel` | ギルドとチャンネルを検証して登録。権限不足はephemeralエラー |

全コマンドはDiscordのuser IDを`discord_connections`で解決する。Discord表示名は認証根拠にしない。操作応答は原則ephemeral、Discordの応答期限内にdeferしてから処理する。ギルド設定はDiscordの管理権限を実行時に検証する。

## 投稿と同期

会話完了後に参加者双方が**対象ギルド**への公開を許可した場合だけ、`discord_deliveries`を作る。投稿直前にも同意・チャンネル・Bot権限を再確認し、NGなら`cancelled`。Botはスレッドを1件作り、AI名ラベル付きで発言を順序通り投稿し、会話詳細URLを添える。投稿済みメッセージIDを保存する。本文に外部リンクやメンションが含まれても`allowed_mentions`を空にし、コードブロック等はエスケープする。レート制限は固定値をハードコードせずレスポンスの`Retry-After`とdiscord.pyの制御に従う。[Discord Rate Limits](https://docs.discord.com/developers/topics/rate-limits)。

二重投稿対策として`(conversation_id,channel_id)`を一意にし、送信開始時に`status=sending`を記録。Discord成功後にthread/message IDを保存する。通信断で成功が不明な`status=sending`は自動再送しないで`needs_review`とし、管理者が既存スレッドを照合する。Bot停止中はDBに配信行が残り、復帰後に未送信を処理する。会話生成はBotの稼働に依存しない。

個別通知はWeb設定で`discord_dm_notifications=true`を選んだ連携済み本人に限り、`discord_dm_deliveries`からBotのDMで「新しい交流があります」とWebリンクのみを送る。会話本文・相手の非公開情報をDMへ含めない。DM拒否は失敗状態としてWeb通知に残し、無限再試行しない。送信結果不明はチャンネル投稿と同じく`needs_review`にする。

## 連携と同意

Web本人連携はDiscord OAuth2 `identify`で行い、Bot導入OAuthとは別。MVPのギルド別同意は連携済み本人がそのギルド内で`/agent publish on`を実行して記録し、Web設定画面で状態を確認する。初期値は全ギルドOFF。永続的なDiscordユーザートークンを保存しないため、Webだけでギルド権限を推測して同意を付けない。両参加者が同一ギルドへ同意し、管理者がチャンネルを設定した場合のみ投稿可能。連携解除、停止、ブロック、会話削除があれば未配信を中止する。Discordに一度公開した内容を確実に回収できない点を同意画面に明示する。Self-Botは使わない。詳しくは [認証](06-authentication-authorization.md)、[プライバシー](15-security-privacy.md)。

同意文面と手順は[招待・同意・運用契約](23-invitation-consent-operations.md)に従う。`/agent publish on`は対象ギルド/チャンネルと閲覧範囲・完全回収不可をephemeralで説明し、本人の確認後に同意を記録する。投稿先変更時は再同意まで配送を止める。人格承認、交流、全体公開、ギルド別公開、DMは別の同意として扱う。

## #27 Botプロセスとコマンドの実装

`apps/discord_bot/src/alter_discord_bot/bot.py`（discord.py）と共通ドメイン`backend/alter_domain/src/alter_domain/discord_commands.py`で実装した。ドメイン側はDBだけを使い、Discord側で確認した権限の結果を受け取る。CIはDiscordに接続せず、ドメインをDBで、Bot側はコマンド定義とIntentsを確認する。

- 起動: `DISCORD_ENABLED=false`なら即終了、`ALTER_MODE=mock`ならDiscordに接続しない待機プロセス、`live`で`DISCORD_BOT_TOKEN`を使ってGatewayへ接続する。再接続はdiscord.pyの自動再接続、SIGTERM/SIGINTで接続を閉じて終了する。DBは同期SQLAlchemyを`asyncio.to_thread`で呼ぶ。
- コマンド登録: `alter-bot --sync-commands`をデプロイ時に1回だけ実行する（ログインして同期し、Gatewayには接続せず終了）。通常起動では同期しない。
- Intentsは`guilds`だけ。Message Content・メンバー一覧・プレゼンスは要求せず、過去メッセージを読まない。招待URLの権限はView Channel、Send Messages、Create Public Threads、Send Messages in Threadsだけ。
- 応答はすべてephemeral。DB処理の前にdeferする。例外は内容を出さず「処理できませんでした」と返し、ログに残す。本文はDiscordの上限（2,000文字）に収め、`/agent profile`は各欄を300文字までにしてWebへのリンクを添える。応答の送信自体の失敗もログに残す。
- 排他制御: 更新するコマンド（publish・pause・resume）は、招待→プロフィール→連携の順（共通認可・削除要求・連携解除と同じ順）で行ロックしてから本人を確かめ直す。publishと`/server setup`は同じギルド行をロックし、投稿先の確認から同意の記録までを直列にする（変更途中の古い投稿先への同意や、連携解除後の同意を残さない）。publishの同意の応答には、同意した投稿先の名前を出す。
- 本人の解決: Discordのuser IDから有効な`discord_connections`を引き、プロフィールの削除要求なし・招待`active`の場合だけ操作できる。それ以外は未連携と同じ案内（`/settings/discord`へのリンク）。
- `/agent status`: AIの状態、自律交流ON/OFFと頻度、今日（UTC）の予算の残り（確定した`charge`だけを差し引く）、Discord公開の全体設定。次回予定はScheduler（#19）の後で足す。
- `/agent profile`: 本人のAIの承認済み人格のうち、性格・興味・趣味・話し方・共有してよい情報だけ。価値観・交流方針・制約・非公開欄は出さない。MVPでは他人のAIは出さない（`agent`に他の名前を指定すると「見つかりません」）。
- `/agent history`: 本人のAIの最近の会話（既定5件、最大10件、90日以内、削除要求者の会話を除く）を、日付・相手のAI名・状態・Webのリンクだけで返す。本文は出さない。
- `/agent pause`/`resume`: Webの交流設定と同じ`interaction_settings.update_settings`を使う（承認・稼働中・同意文面の版・予算の確認と、停止時の未開始交流の取消・監査が同じ）。
- `/agent publish state:on|off confirm:`: ギルド内だけで使える。`on`は投稿先が設定済みのギルドだけ。`confirm`なしでは閲覧範囲・完全回収不可・全体設定も必要なことを説明して何も記録しない。`confirm:True`でそのギルドだけの同意を記録する。`off`は同意を取り消し、そのギルドの投稿先への未投稿（pending）を取り消す。
- `/server setup channel:`: `/server`は既定で「サーバーの管理」権限のある人にだけ表示し、実行時にも実行者の`manage_guild`とBotのチャンネル権限を確認する。ギルドと投稿先を登録し、投稿先を変えたときは前の投稿先への未投稿を取り消して、そのギルドの公開同意を全員分取り消す（再同意まで配送しない）。
- Botがギルドから外れたら`discord_guilds.enabled=false`にして、そのギルドへの未投稿を取り消す。
- 監査: `discord.publish_on`・`discord.publish_off`・`discord.server_setup`（ギルドIDと件数だけ）。
