# デザインシステム

## 方針

「自分のAIの活動を安心して観察できる」を軸に、AI生成の会話と本人操作を明確に分ける。AI発言には常に「AI分身」ラベルと生成時刻を表示し、人間の本人発言に見せない。公開・停止・削除の操作は結果と取り消し可能性を明記する。ブランド名、ロゴ、具体的な色値はデザイン担当がコードで決定する（ADR-015）。正式トークンは `apps/web/app/globals.css` のCSS変数とし、全画面で共有する。移行中: 2026-11-12時点で新デザインのトークンはトップページ用の `apps/web/app/landing.css` にだけあり、`globals.css` へ移してログイン後の画面にも広げる作業を #5 で進めている。それまで他画面は仮トークンのまま。

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

## デザイン成果物

デザイン担当はコードでブランド・色・画面を作り、PRでレビューする。成果物は、色・タイポ・余白のトークン（CSS変数）、上表のvariant、15画面のPC/mobile実装とその画像。主要3フロー（作成、交流確認、Discord公開）は実画面（開発サーバーまたはPreview）で確認する。文言と状態は [画面仕様](12-frontend-screens.md) に反映する。未決定のロゴや配色がAPI・DB実装を止めないよう、構造と文言を先に決める。[チーム運営](17-team-development.md)。
