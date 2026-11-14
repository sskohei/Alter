# Web Preview / Productionの接続手順（#35）

2026-11-14: 最新mainの正式プロフィール・人格・活動・設定・記憶・Discord・アカウントAPIとWebを統合した。
Vercel接続・callback登録・実OAuthの成功は未確認。#33/#34もOpenであり、設定ファイルとMock検証だけでは#35をcloseしない。
依存は #3/#33/#34/#23/#24/#25/#29/#31。自律交流の実接続には#19/#20/#28も必要。Mockを本番APIとして配置しない。

## Vercelの登録

1. #33でPreview/Productionの別Supabaseプロジェクト、#34でRender APIを用意する。
   #4の公開条件、#36の監視とrollback担当を確定する。
2. VercelでこのGitリポジトリを選択し、Root Directoryを`apps/web`、FrameworkをNext.js、Node.jsを24.xに設定する。
   workspaceの`packages/api-client`とroot scriptsを参照するため、Root Directory外のソースを含める設定を有効にする。
   install/buildは`apps/web/vercel.json`を使い、root lockfileによるfrozen installを維持する。
3. `config/web/preview/.env.example`と`config/web/production/.env.example`を環境別に登録する。
   テンプレート値を実値に置換し、Publishable Key以外のbackend秘密はWebへ登録しない。
   Vercelの公開システム変数の自動露出は無効にする（NEXT_PUBLIC_*は検査のallowlistのみ）。
   反対環境のSupabase URLも公開URLとして登録し、プロジェクト混同を検査する。
4. Previewは固定aliasを登録し、`NEXT_PUBLIC_SITE_URL`をそのHTTPS Originに合わせる。
   登録していないブランチ固有URLではOAuth受け入れをしない。API URLも同じ環境へ揃える。
   `ALTER_REGISTERED_WEB_ORIGINS`は運営が実登録を確認したOriginのCSVで、APIのCORS設定そのものではない。
5. `git.deploymentEnabled:false`でGit自動デプロイは停止したままとする。
   手動のPreviewデプロイもbuild時に環境検査を通す。公開担当がプラン・有料席・ドメイン・費用を実利用条件で記録する。
   このPRはプラン契約・プロジェクト作成・ドメイン購入を行わない。

## CallbackとCORS（B/F共同確認）

| 設定先 | Preview / Productionそれぞれに登録する値 |
|---|---|
| Supabase Auth Site URL | その環境のWeb Origin |
| Supabase Redirect URLs | `https://<web>/auth/callback`、`https://<web>/auth/reauth/callback`（next等のqueryを含む実URLで確認） |
| Google / GitHub OAuth App | その環境のSupabase `https://<project>/auth/v1/callback` |
| Discord OAuth App | その環境のWeb `https://<web>/auth/discord/callback` |
| API CORS_ORIGINS | 登録したWeb Originの完全一致。credentials/authorizationと必要method/headerのみ。wildcard不可 |
| API DISCORD_REDIRECT_URI | Webに登録したDiscord callbackと同じ値 |

APIの`SUPABASE_URL`、`SUPABASE_JWT_ISSUER`、`SUPABASE_JWT_AUDIENCE`、`SUPABASE_SECRET_KEY`を含む環境対応表は
[APIの環境別設定](14-deployment-infrastructure.md)を参照する。
`SUPABASE_STORAGE_SECRET_KEY`、`ANTHROPIC_API_KEY`、人格暗号化鍵・作成再送HMAC鍵もWebへ渡さない。

Preview/Productionは別OAuth Appを基本とし、本番利用者のトークンやDBをPreviewで使用しない。
招待実データはADR-014に従いDB＋運営CLIのみで管理し、環境変数・Gitへコピーしない。
WebはBFF経由でBearerを転送する。CORS成功を所有権認可の代わりにしない。
SSR/proxy/BFFと失敗callbackはprivate/no-store、認証データをCDN共有キャッシュへ入れない。
logoutでOAuth/削除確認の短命cookieも消す。Nextの全応答にsame-origin Referrer-Policy/nosniff/frame denyを付ける。

## 検証と本番への適用

- `pnpm web:check-env`：Vercel上では自動実行。ローカル検証では秘密をログ出力しないシェルから環境を渡し、
  `VERCEL_ENV=preview`または`production`を明示する。値の表示はしない。
- `ALTER_SMOKE_WEB_ORIGIN=https://<web> ALTER_SMOKE_API_ORIGIN=https://<api> pnpm web:smoke`：
  認証なしGET/OPTIONSだけでログイン、保護画面復帰、BFF 401、no-store、安全なcallback失敗、
  CORS許可/拒否、API未認証401、再認証callback、live OpenAPIの主要経路を検査する。秘密・会話本文を出力しない。
  `/openapi.json`は#34の契約どおり全環境で公開する。Productionの`/docs`・`/redoc`だけ無効。
- #37のstaging手順で両OAuth、作成→承認→交流→履歴→Discord、再確認/削除、401復帰、logout後の別ユーザー、
  375/320pxと配信JSの秘密混入を実確認する。上の認証なしスモークやMock成功だけで代替しない。
- preproductionの受け入れ証跡と担当者レビュー後、限定本番スモーク用に
  `ALTER_APPROVED_PRODUCTION_COMMIT`へ対象40桁SHAを設定する。Vercelが提供する`VERCEL_GIT_COMMIT_SHA`との一致が必須。
  CLIデプロイの場合も実際のソースSHAをVercelのGitメタデータとして保持し、任意の別SHAを渡さない。
- 限定本番スモーク成功と#37 release gate後だけ招待者へ案内する。自動デプロイの有効化はその後の明示的な運用判断。
  失敗時は案内を止め、AI交流/配信を止めて前回の検証済みWebへ戻す。DBを古いバックアップへ安易に戻さず、
  削除/失効再適用は#30/#36の手順に従う。

接続後に記録するもの：プロジェクト名、Preview/Production URL、対象SHA、API/Supabaseの環境対応、
プラン・席数と見積日、callback/CORS確認担当、実行日時・結果・rollback先。秘密と招待identityは記録しない。
実環境での受け入れは未記録。本番公開必須ゲートは2026-11-17または公開前日の早い方までに確認し、未完了なら延期する。

参照：[Vercel monorepos](https://vercel.com/docs/monorepos)、
[Git deploymentEnabled](https://vercel.com/docs/project-configuration/git-configuration)、
[環境変数](https://vercel.com/docs/environment-variables)、[構成](14-deployment-infrastructure.md)。

Referrer-Policyは通常画面では`same-origin`を採用する。`no-referrer`を全画面に適用すると、
JavaScript起動前の同一Origin form POSTでOriginがnullとなりNextのCSRF検査に拒否されるため。
callback自身はno-referrerを維持し、外部へのReferer送信を防ぐ。
JavaScript無効のOAuth→logoutも回帰検査する。


## #35をcloseするための受け入れ記録

環境ごとに次の表を埋め、PR #53へ結果を転記する。失敗・未実施があればcloseしない。
証跡には秘密・cookie・OAuth code/state・個人情報を含めず、認証済みHARは添付しない。

| 項目 | Preview | Production |
|---|---|---|
| Web / API Origin、Supabaseプロジェクト、対象SHA | 未確認 | 未確認 |
| Vercel Root Directory・workspace build、プラン/席数/見積日 | 未確認 | 未確認 |
| Google / GitHub成功callback、キャンセル復帰、再認証 | 未実施 | 未実施 |
| Discord本人連携・解除とcallback（テスト用本人/ギルド） | 未実施 | 未実施 |
| `web:check-env` / `web:smoke`の実行日時・結果 | 未実施 | 未実施 |
| 作成→承認→交流→会話閲覧（実API/Worker、ブラウザを閉じて確認） | 未実施 | 限定スモーク未実施 |
| 401再送・ログアウト→別ユーザー、SSR/CDNでデータ混在なし | 未実施 | 未実施 |
| 375px/320px、公開env/配信JSの秘密混入なし | 未実施 | 未実施 |
| B/F確認担当、rollback先、#37公開ゲート | 未確認 | 未確認 |

公開変数はbuild時に固定されるため、Previewでbuildした成果物をProductionへそのまま昇格しない。
Productionの環境でレビュー済みの同じソースSHAを再buildし、限定スモークを実行する。
プラン/席数/料金は契約者が実利用条件で確定する。現時点で特定プランの契約や金額の受け入れは行っていない。
