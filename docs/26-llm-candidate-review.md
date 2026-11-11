# LLM候補の公開情報調査（#14）

2026-11-11確認。採用決定ではない。Directoryで見つけたOpenRouterについて、
[公式モデルカタログ](https://openrouter.ai/api/v1/models)の公開メタデータを取得した。
取得日時/単価/追加価格条件は`evaluations/llm/catalog-2026-11-11.json`に固定する。

| カタログ上のモデルID | 入力USD/100万tokens | 出力USD/100万tokens | 構造化出力（宣言） |
|---|---:|---:|---|
| anthropic/claude-haiku-5.5 | 0.10 | 0.50 | 対応 |
| google/gemini-4-carbon | 2.00 | 10.00 | 対応 |
| anthropic/claude-fable-5.2 | 8.00 | 40.00 | 対応 |

宣言上の対応は実際のschema遵守率・日本語品質・安全性を保証しない。
[構造化出力の公式仕様](https://openrouter.ai/docs/guides/features/structured-outputs)では、
対応モデルにjson_schemaを指定する。非対応endpointへのfallbackを禁止する設定/検証が必要。

## 費用シナリオ（実測値でも最大額保証でもない）

1体あたり4発言＋要約相当の予備1回、各回input 1000/output 200 tokensと仮定する。
初回＋最大3再試行をすべて消費するとinput 20000/output 4000 tokens。
上のカタログ単価では1体1会話あたり順に$0.004/$0.080/$0.320。
同一モデルの2体なら2倍、10体が日次2回なら20倍で$0.080/$1.600/$6.400。
$0.05/体/会話の初期枠には後2候補がこのシナリオで収まらない。
人格提案、追加moderation、reasoning、課金結果不明の再試行や価格段階/手数料は別途評価する。
実際のMVP要約は#22で追加LLMなしの引用を採用する提案なので、予備を含む比較用計算とする。
実入力token上限・出力上限・reasoningと提供endpointの確定前に「必ず枠内」と判断しない。

## データ利用条件と未検証

[公式データ収集説明](https://openrouter.ai/docs/guides/privacy/data-collection)では、
通常のログ/改善利用はopt-inだが、CSAM確認やSafety Retention endpoint等の例外がある。
経由先provider側の条件も確認が必要。ZDR設定やprovider固定を含む運用確認が終わるまで、
「保持なし」「学習利用なし」と断定せず、ユーザーデータを送信しない。

正式採用は保留。評価セット5ケースの合成データで日本語/終了希望/注入耐性/schemaを実評価し、
F/Dの品質レビュー、遅延p50/p95、endpoint別の保持/学習条件、moderation、実費を記録する。
安価な候補を評価開始点とするが、安全性や品質を価格から推測しない。
運用責任者sskoheiが採用/公開直前とモデル変更時に公式価格・規約・endpoint設定を再確認する。
