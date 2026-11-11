# MVP後の計測と改善判断（#38）

状態: 計測コードと判定基準のドラフト。#37の本番公開・7日間の実測は未完了。
ローカルの模擬会話は実測や品質合格に数えない。MVP公開をこのIssueでブロックしない。

## 集計

```bash
# DATABASE_URLはサーバー専用のread-only接続。実値をログ/リポジトリへ出さない。
uv run --package alter-domain alter-ai-metrics --output /tmp/alter-ai-metrics.json
# 本番公開後、実際に観測した日数だけを指定する。
uv run --package alter-domain alter-ai-metrics --environment production --observation-days 7 \
  --output /tmp/alter-ai-metrics-production.json
```

コマンドはrepeatable read/read only transactionで集計し、本文・人格・ID・トークン・秘密を出力しない。
出力は環境/観測日数、会話状態別件数、記憶件数とAIごとの最大件数、削除出典抑止件数、
待機job数/最古待機秒、不明呼出数、DB接続/サイズ/統計reset時刻/blocks/deadlocks、台帳の累積実費。
会話/記憶/jobは保持期間内のスナップショット、費用/DB統計は累積値なので、
日次の差分とreset時刻を併せて評価する。未知呼出は失敗会話に残ったstarted/unknownを数える。
0件の成立率・正解記憶なしのrecallはnull（成功100%と解釈しない）。
運用責任者sskoheiが1日1回・7日以上取得し、障害日と設定変更を別途記録する。

## 評価の入口（自動導入はしない）

| 対象 | 必要な証拠 / 評価を始める暫定閾値 | 評価に含めるもの |
|---|---|---|
| 記憶検索 | 本番7日以上、AI最大300件超、合成/許諾済み30ケース以上でrecall@5<0.8 | キーワード改善と意味検索の再現率/誤記憶、埋込費用、私的情報の除外、削除反映 |
| 記憶統合 | 重複候補率と訂正/削除件数を本人レビューで測る（未計測） | 出典保持、本人確定事実への昇格禁止、削除済み内容の復活防止 |
| 関係グラフ | リストでの相手探索の操作テスト（未計測） | mobile/キーボード、関係性の誤認防止、Figma |
| 通知更新 | 15〜30秒Pollingでの体感遅延とAPI負荷（未計測） | SSEの再接続/認可/更新費用、Polling維持との比較 |
| ジョブ基盤 | 本番7日以上、最古待機300秒超が継続するなら評価 | Worker増強/SQL改善と別queueの比較、再試行/冪等性/復旧・費用 |

`metrics.recall_at_k`は重複検索結果を1件と数える。
`evaluation_gates`はlocal/7日未満で必ずinsufficient_evidenceを返す。
本番の閾値超過はevaluate（比較開始）であり、導入決定ではない。
DB負荷の評価はsnapshot差分＋Render/DBのCPU/I/O・実行計画と合わせ、単一時点で判断しない。

## 改善Issueへの引き渡し

現在は全案を保留（実測不足）。本番の計測値、品質/操作評価、費用見積もりを添えてF/B/Dで判断し、
採用案だけを1〜2日でレビュー可能な子Issueに分割する。
子Issueには目的、測定値/採用・見送り理由、API/DB/Figma、所有権/同意/削除、テスト、移行/rollback、
依存Issue、費用上限を記載し、ADR/ロードマップを同時更新する。
#38はこのPRだけで完了扱いにせず、#37の公開と実測後に進める。
