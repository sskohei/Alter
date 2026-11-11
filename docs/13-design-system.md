# デザインシステム

## 方針

「自分のAIの活動を安心して観察できる」を軸に、AI生成の会話と本人操作を明確に分ける。AI発言には常に「AI分身」ラベルと生成時刻を表示し、人間の本人発言に見せない。公開・停止・削除の操作は結果と取り消し可能性を明記する。ブランド名、ロゴ、具体的な色値はデザイン担当がコードで決定する（ADR-015）。正式トークンは `apps/web/app/globals.css` のCSS変数とし、全画面で共有する。トップページ固有の演出（揺れ・3D・HUD）だけは `apps/web/app/landing.css` に置く。

## デザイン部品と実装の対応

| デザイン部品 | shadcn/ui実装起点 | 必須variant |
|---|---|---|
| Primary/Secondary/Danger Button | `Button` | default, disabled, loading, focus |
| Field/Textarea/Help | `Input`, `Textarea`, `Label`, `Form` | normal, error, disabled |
| Setting Switch | `Switch` | on/off, pending, disabled |
| Confirmation | `AlertDialog` | 公開同意、削除、停止 |
| Notice/Status | `Alert`, `Badge` | success, warning, error, AI表示 |
| Conversation Card | `Card` + 独自Message | 参加者別、進行中、失敗 |
| Navigation | `Sidebar`, `Sheet`, `Tabs` | desktop/mobile |
| Data List | `Table`またはリスト | loading, empty, paged |

デザイントークンはCSS変数で色、文字サイズ、余白、角丸、影を定義。本文16px基準、タップ領域44px以上、色だけに依存しない状態表示。WCAG 2.2 AAのコントラスト・フォーカス表示を目標とし、キーボードだけでオンボーディングから停止まで完了できるようにする。会話はDOM順と視覚順を一致させ、`aria-live`は新着通知のみに限定して連続読み上げを防ぐ。

### トークン一覧（`apps/web/app/globals.css` の `:root`）

方針: グレーを混ぜたくすみ色と紙色の地、沈ませる暗い面、彩度最大の色は `--line` の1色だけで「線と小さな印」（フォーカス・選択・現在地・番号）に限って使う。

| 変数 | 値 | 用途 |
|---|---|---|
| `--paper` / `--paper-2` | `#E4E0D7` / `#EEEBE4` | ページの地 / カード・入力欄など一段上のシート |
| `--ink-0`（`--ink`） | `#17161B` | 文字・細線。`--ink` は面ごとに切り替わる現在の文字色 |
| `--dim` | `#524E57` | 補足の文字（紙色の上で6.2:1） |
| `--sink` | `#24252B` | 沈ませる暗い面（文字は明るく反転） |
| `--rose` / `--sage` / `--lilac` / `--mustard` | `#E3A69C` / `#A9BBA2` / `#B4A9CE` / `#D8B85C` | くすみ色の面・状態の下地。黒文字で7:1以上 |
| `--line` / `--line-ink` | `#0026FF` / `#FFFFFF` | 唯一の高彩度色（紙色の上で5.9:1）/ その上の文字 |
| `--danger` | `#A1261B` | エラー・危険操作（紙色の上で5.7:1）。色だけに頼らず文言・記号も添える |
| `--display` / `--jp` / `--jp-thin` / `--mono` | Unbounded / Zen Kaku Gothic New / M PLUS 1 / Space Mono | ロゴ・英字の大見出し / 見出し / 本文 / ラベル・コード |
| `--w-thin` | `100` | 日本語本文の太さ（極細）。読みにくい箇所は個別に上げる |
| `--sq` / `--sq-title` | `1.12` / `1.06` | トップページの平体（横に広げる量） |
| `--radius` / `--hair` | `6px` / `.5px` | 角丸 / 細線 |

状態の表し方: お知らせ（`.notice[role=status]`）はライラックの下地＋左の線、エラー（`.notice[role=alert]`）はローズの下地＋`--danger`の左線、入力エラーは`--danger`の枠と「!」付きの文言。現在のページはナビの左（スマホは下）に`--line`の線と`aria-current="page"`。


## デザイン成果物

デザイン担当はコードでブランド・色・画面を作り、PRでレビューする。成果物は、色・タイポ・余白のトークン（CSS変数）、上表のvariant、15画面のPC/mobile実装とその画像。主要3フロー（作成、交流確認、Discord公開）は実画面（開発サーバーまたはPreview）で確認する。文言と状態は [画面仕様](12-frontend-screens.md) に反映する。未決定のロゴや配色がAPI・DB実装を止めないよう、構造と文言を先に決める。[チーム運営](17-team-development.md)。
