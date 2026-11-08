# Web Preview / Productionの接続手順（#35 Draft）

2026-11-08時点でVercelプロジェクト・Web URL・Render API・Supabase公開URLは未設定（利用者確認済み）。
このPRは設定ファイル・検査・手順の実装であり、実デプロイやIssue全体の受け入れ完了ではない。
依存は #3/#33/#34/#23/#24/#25/#29/#31。live APIの業務経路も未完成なのでMockを本番APIとして配置しない。

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
  CORS許可/拒否、live OpenAPIの主要経路を検査する。秘密・会話本文を出力しない。
  `/openapi.json`は限定スモーク時に確認可能にする。公開しない構成ならBが同等の私的確認方法を用意して記録する。
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
現時点ではすべて未設定/未実施。本番公開必須ゲートは2026-11-17または公開前日の早い方までに確認し、未完了なら延期する。

参照：[Vercel monorepos](https://vercel.com/docs/monorepos)、
[Git deploymentEnabled](https://vercel.com/docs/project-configuration/git-configuration)、
[環境変数](https://vercel.com/docs/environment-variables)、[構成](14-deployment-infrastructure.md)。

Referrer-Policyは通常画面では`same-origin`を採用する。`no-referrer`を全画面に適用すると、
JavaScript起動前の同一Origin form POSTでOriginがnullとなりNextのCSRF検査に拒否されるため。
callback自身はno-referrerを維持し、外部へのReferer送信を防ぐ。
JavaScript無効のOAuth→logoutも回帰検査する。
