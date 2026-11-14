# Git・GitHub運用ガイド

この文書は [チーム開発方針](17-team-development.md) のGit操作を具体化する。3人の担当範囲は同文書、Issueの分割は [Issue計画](19-github-issues.md) を参照する。

## ブランチ

`main`が唯一の長期ブランチで、常にデプロイ可能な状態を保つ。`develop`は設けない。通常の変更はIssueを作り、最新の`main`から短命ブランチを切る。ブランチ名は次の形式とする。

`<種別>/<Issue番号>-<英小文字・数字・ハイフンの短い説明>`

| 種別 | 用途 | 例 |
|---|---|---|
| `feature` | 新機能 | `feature/12-conversation-api` |
| `fix` | 不具合修正 | `fix/34-auth-refresh` |
| `docs` | 文書のみ | `docs/45-git-workflow` |
| `chore` | CI・依存更新・構成 | `chore/46-ci-checks` |
| `refactor` | 挙動を変えない整理 | `refactor/47-job-repository` |
| `test` | テストのみ | `test/48-worker-retry` |
| `hotfix` | 本番の緊急修正 | `hotfix/49-discord-permission` |

説明部分はASCII小文字・数字・ハイフンのみ、単語3〜5語程度。担当者名は入れない。例外的な緊急修正もIssueを追跡し、`main`から分岐して同じPRレビューを通す。複数Issueを1ブランチにまとめない。

## 作業開始からPRまで

初回コミットと`origin/main`がある状態では、以下を使う。現時点のように初回コミット前なら、管理者が最初の文書・設定を`main`に登録してからこの手順へ移る。

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feature/12-conversation-api
```

編集後は差分を確認し、関連ファイルだけをstageする。`git add .`で他人の未コミットファイルや秘密を巻き込まない。

```bash
git status --short
git diff --check
git add apps/api/ docs/07-api-design.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat(api): add conversation endpoints (#12)"
git push -u origin feature/12-conversation-api
```

コミット件名は`feat|fix|docs|chore|refactor|test`の形式（緊急修正は`fix`）、スコープは`web`/`api`/`db`/`worker`/`bot`/`design`など。1コミットに意味のある変更をまとめる。生成コードやlockfileは関連する変更と同時にコミットし、`.env`、DBダンプ、ビルド成果物はコミットしない。

## Pull Request

PRのベースは`main`。下書きPRを早めに開き、完了時にReady for reviewへする。本文にはIssue、目的、主な変更、受け入れ条件、実行したテストと結果、未検証事項を記載する。UI変更はPC/mobileの画像、API変更はOpenAPI差分、DB変更はmigrationと戻し方を添える。原則1〜2日でレビュー可能な大きさにし、機能追加と無関係な整形を混ぜない。

マージ条件はCI成功と作成者以外1名の承認。権限・公開・削除・費用上限はフロント/バック両方の視点で確認する。GitHubのブランチ保護で`main`への直接push、force push、未承認マージを禁止する。マージ方式は**Squash and merge**、マージ後は作業ブランチを削除する。

## `main`の更新と競合

作業中に`main`が進んだら、個人/共有ブランチの履歴を書き換えずに取り込む。競合箇所を修正し、テストを再実行する。

```bash
git fetch origin
git switch feature/12-conversation-api
git merge origin/main
# 競合があれば対象ファイルを修正してから
git status
git add <解決したファイル>
git merge --continue
git push
```

誤って競合解決を始めた場合は`git merge --abort`で戻せる。`main`や他人のブランチへforce pushしない。PRがマージされたら`git switch main && git pull --ff-only origin main`で更新し、`git branch -d <作業ブランチ>`でローカルを整理する。

## Issueと変更順

Issueには主担当、目的、受け入れ条件、依存Issue、影響するAPI/DB/デザインを記す。API契約やDB migrationを先に小さなPRで確定し、フロントは同じ契約のMockで並行実装する。破壊的なDB変更は、追加→アプリ移行→旧列削除を別PR・別デプロイで進める。仕様判断が変わるPRでは [ADR](decisions/architecture-decisions.md) と関連する仕様書を同時に更新する。

## GitHub設定と必須チェック（Issue #3）

2026-11-02に[MainRule](https://github.com/sskohei/Alter/rules/26346738)を確認・更新した。作成直後は`disabled`で、承認数0・必須CIなしだった。現在は`active`、対象は`refs/heads/main`、bypass対象はなし。次をGitHub側で強制する。

- PR経由の変更、作成者以外1名の承認、追加push時の古い承認の破棄、レビュー会話の解決。
- 必須チェック **`CI required`**（GitHub Actions提供）。ベースブランチの最新変更を取り込んでからマージする。全5ジョブ成功が必要で、検査内容と将来の追加項目は[テスト戦略](16-testing-strategy.md)を参照。
- `main`の削除・force push禁止とlinear history。PRのマージ方法はSquashのみ。

リポジトリ全体もSquash mergeだけを許可し、マージ後のブランチ自動削除を有効化した。`Restrict updates`はPRマージも禁止してしまうため使用せず、必須PR規則で直接pushを禁止する。署名コミット必須化は本Issueの要件に含めず、署名環境を前提にしない。必須チェック名を変更する場合は、同時にMainRuleのチェック名を更新し、GitHub Actionsのチェック結果と一致することを確認する。

PR本文には[テンプレート](../.github/pull_request_template.md)を使い、Issue・受け入れ条件・実行結果・未検証事項・UI/API/DB差分を埋める。Actionsが成功しても別の担当者の承認前にはマージしない。GitHub標準secret scanningが利用できない制約とGitleaksによる代替運用は[テスト戦略](16-testing-strategy.md)に記録している。
