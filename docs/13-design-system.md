# デザインシステム

## 方針

「自分のAIの活動を安心して観察できる」を軸に、AI生成の会話と本人操作を明確に分ける。AI発言には常に「AI分身」ラベルと生成時刻を表示し、人間の本人発言に見せない。公開・停止・削除の操作は結果と取り消し可能性を明記する。ブランド名、ロゴ、具体的な色値はデザイン担当がFigmaで決定し、正式トークン化するまで仮置き扱い。

## Figmaと実装の対応

| Figmaコンポーネント | shadcn/ui実装起点 | 必須variant |
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

## デザイン成果物

デザイン担当はブランド1枚、色・タイポ・余白トークン、上表のvariant、15画面のPC/mobile、主要3フロー（作成、交流確認、Discord公開）のクリック可能プロトタイプを納品する。ハンドオフ時はFigma frame URLをIssueに記し、文言と状態を [画面仕様](12-frontend-screens.md) に反映する。未決定のロゴや配色がAPI・DB実装を止めないよう、構造と文言を先に渡す。[チーム運営](17-team-development.md)。
