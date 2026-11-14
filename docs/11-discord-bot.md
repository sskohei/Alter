# Discord Bot仕様

## 構成と権限

discord.py 2.xのGatewayクライアントをRender有料Background Workerで常時実行する。Botは共通PythonドメインサービスとDBを利用し、FastAPIプロセスに依存しない。`bot`と`applications.commands`でギルド導入し、必要な権限はView Channel、Send Messages、Create Public Threads、Send Messages in Threads。管理者権限そのものは要求しない。Webhookによる名前/アイコン切替は正式版候補で、MVPはBot名で「AI名: 発言」を投稿する。Message Content privileged intentや過去メッセージ収集は使わない。[Discordコマンド](https://docs.discord.com/developers/interactions/application-commands)、[Webhook](https://docs.discord.com/developers/resources/webhook)（2026-10-27確認）。

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

## #28 会話投稿とDM通知の実装

共通ドメイン`backend/alter_domain/src/alter_domain/discord_delivery.py`（DBだけ）と、Botの送信ループ`apps/discord_bot/src/alter_discord_bot/delivery.py`で実装した。CIはDiscordに接続せず、ドメインをDBで、送信ループを偽のDiscordで確認する。

- 作成: 会話の完了トランザクションで、`activity.notify_conversation_completed`のあとに`discord_delivery.enqueue_after_completion`を呼ぶ（Worker #19/#61が接続する）。投稿先は1会話につき1つで、条件を満たす投稿先が1つなら`pending`で1件、2つ以上（両者が複数のギルドに同意している）ならどれにも送らず候補を`needs_review`（`ambiguous_target`）で残し、運営・管理者が選ぶ（docs/09）。取り出し時に候補が2つ以上に増えていた場合も同じく保留する。DMは完了通知1件につき1件。再実行しても増えず、取り消し済みの行は復活させない。投稿先を変えたあとは、新しい投稿先への行を別に作れる。
- 投稿の条件（作成時と取り出し時の両方で確認）: 会話が完了・Webで見える範囲（90日・削除要求なし・削除済みAIなし）、投稿先とギルドが有効、参加者2人とも「会話開始時のDiscord公開の同意（`conversation_participants.discord_consent`）・AIの全体設定ON・稼働中のAI・削除要求なし・有効な招待・有効な連携・そのギルドへの有効な同意」、どちらもブロックしていない。満たさなくなった行は`cancelled`（`last_error='not_eligible'`）。
- DMの条件: 本人がDM通知ON・削除要求なし・有効な招待・有効な連携（作成時と同じDiscordユーザー）、通知が90日以内で会話が見える範囲。DMは完了通知に結び付くため、Web通知をOFFにしている人には作らない。本文は固定文言と会話詳細のリンクだけ。
- 取り出し: `for update skip locked`で1件だけロックし、条件を確認し直して`sending`にしてコミットしてから送る。確認し直しの読み取りでは同意・連携の行をロックしない（連携解除などは「連携→同意→配信」の順でロックするため、配信のあとに同意をロックすると交差する）。同時に解除された場合、先に`sending`になった行は送信を続け、解除側は`pending`だけを取り消す。
- 書き込みの経路: スレッド作成・発言・DMは、discord.pyではなく内部で再送しないREST呼び出し（`alter_discord_bot.rest`）で送る。discord.pyは5xxや接続断で書き込みも自動で再送し、作成が成功して応答だけ失敗したときに二重のスレッドや発言ができるため。再送するのは処理されていないことが確かな429だけで、`retry_after`/`Retry-After`に従って待つ（最大60秒・3回）。チャンネルの取得や権限の確認、スレッドの削除（何度試しても重複しない）はdiscord.pyを使う。
- 投稿: Botの権限を投稿直前にも確認し、公開スレッドを1件作ってすぐスレッドIDを保存する。冒頭で「AIが生成した会話」と示し、各発言は「🤖 AI名（AI）」のラベル付きで順に送る（`moderation_status='allowed'`の発言だけ）。AI名と本文はMarkdown・メンション・リンクの記号をエスケープし、`allowed_mentions`は空、URLの埋め込みは出さない。長い発言は2,000文字以内に分ける（エスケープの途中では切らない）。最後にWebの会話詳細のリンク（参加者本人だけが開ける）。成功したら最初のメッセージIDを保存して`sent`。
- 失敗の扱い: Discordが拒否した失敗（403/404/その他4xx）は`cancelled`。何も書き込んでいない段階（チャンネル・ユーザーの取得）の5xx・通信断は再試行（2^回数分、最大60分、5回で`cancelled`・`gave_up:`）。作成・送信の要求を出したあとの5xx・429・通信断、スレッドを作ったあとの失敗は、成功したか分からないので`needs_review`（自動で再送しない）。DMを受け付けない設定は`cancelled`（`dm_forbidden`）で、Web通知は残る。`sending`のまま10分たった行（Botの異常終了など）も`needs_review`。`last_error`には分類したコードだけを残し、本文や例外の詳細は残さない。
- 取り消し: DM通知のOFFで本人あての未送信DMを同じトランザクションで取り消す。AIの全体設定（`discord_publish`）・自律交流のOFFとブロックでの未投稿の取り消しは、#24（PR #48）の`interaction_settings.cancel_pending_posts`がそのまま担う。連携解除・停止・削除要求・ギルド別同意のOFF・投稿先変更・ギルド離脱は#26/#27の処理で取り消す。
- 送信ループ: Botの接続後に5秒ごと、止まった送信中の回収のあと投稿とDMをそれぞれ最大5件送る。`--sync-commands`のときは動かさない。
- `needs_review`の照合: 管理者が`discord_thread_id`（保存済みなら）でスレッドを確認する。再送や取消の運営CLIは#36で検討する。
