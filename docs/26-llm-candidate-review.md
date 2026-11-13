# Claude直接APIによるモデル選定（#14）

2026-11-13。ユーザーが購入済みのClaude APIクレジット（購入時$5）を使い、
招待制10人MVPに絞る方針を承認した。残高・有効期限は未確認。
OpenRouter経由と複数社比較は採用しない。11/11のカタログは過去の調査資料として残す。

## 正式採用（2026-11-13）

決定者sskoheiが限定評価を確認し、Sonnetの正式採用と実装/pushを指示した。
Anthropic直接APIの`claude-sonnet-5-5`を採用する。
[公式モデル仕様](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)を11/13に確認。
API version `2026-10-21`、adaptive thinking / effort low、max_tokens 512（thinking込み）、
入力上限8192、キャッシュ/外部ツールなし、temperature/top_p/top_kは送らない。
設定と価格の正本は`backend/alter_domain/src/alter_domain/llm_selection.py`。
`Utterance`のJSON Schemaを送信し、ローカルで元の型/長さ制約を再検証する。
拒否・打切り・timeout/課金不明は採用せず停止し、未課金を確認できない呼出を自動再試行しない。
[構造化出力](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

根拠は[Sonnet限定評価](../evaluations/llm/sonnet-review-2026-11-13.md)：
単発5ケース＋Haikuで問題があった4会話、31生成、schema成功31/31、
usageからの費用計算$0.098204、生成p50/p95は2.227/5.552秒。
今回の4場面で未記載の体験/未経験の断定は確認せず、自然さ/話者の帰属も改善した。
全10場面/反復評価/人の採点は未実施で、4/5以上を確認済みとは記載しない。
採用モデルの決定と公開受け入れは区別し、残りの品質受け入れは#37で追跡する。

Haikuは2実行123生成でschemaに成功したが、体験の断定が残ったため不採用。
[Haiku評価記録](../evaluations/llm/review-2026-11-13.md)は当時の結果として保持する。
Opusや他社は10人MVPの運用負担と購入済みクレジット活用方針から採用しない。
モデルの自動fallbackも実装しない。

## 単価と10人MVPの見積

[公式単価](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)、2026-11-13確認。
入力$2 / 出力$10（各100万tokens）。thinkingも出力usageで計上する。
キャッシュ割引/無料枠を織り込まない。[費用表](20-cost-estimation.md)を更新した。
1会話の通常仮定は20%予備費込み$0.0564、150会話/月では$8.46。
購入時$5で1か月の運用が保証されるわけではない。上限に達したら生成を停止する。
最大入力/出力を毎回使う見積は既存の会話枠を超えるため、#18で残枠を確認して呼出を止める。

## データ利用・保持

以下はAnthropicの商用API条件であり、Claudeの個人向けチャットの条件とは区別する。
確認日2026-11-13。運用責任者sskoheiが公開直前・モデル/規約変更時に再確認する。

- [学習利用](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training):
  商用APIの入出力は既定で学習に使わない。明示的な同意/フィードバック等の例外あり。
  MVPから学習へのopt-inや会話フィードバックを送らない。
- [保持期間](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data):
  通常は入出力を30日以内に削除。ポリシー違反・法的保存・別契約等の例外があり、
  違反判定された入出力は最長2年、分類スコアは最長7年保持され得る。
  ZDR契約は未取得で、「保持なし」と案内しない。
- アプリで削除しても供給元の保持を直ちに消せると保証しない。公開前の利用説明へ反映する。
  非公開欄/認証情報/メールを入力しない。初回評価はチェックイン済み合成入力のみ。

## moderationの方針と#18への引き渡し

モデルの拒否動作だけをmoderationと見なさない。
#18で入力の公開項目限定、相手発言/記憶の引用扱い、schema/長さ/PII/秘密検査に加え、
危険・攻撃・性的内容等のアプリ方針に沿った判定を実装し、判定不能/違反時は停止する。
判定に別LLM呼出が必要なら同じClaude直接APIを使い、そのtokens/費用も事前予約へ含める。
BaselineModeratorだけでlive公開しない。分類基準・Mock回帰・実評価は#18の残件。

## 状態と引き渡し

Sonnet正式採用は決定済み。採用設定・評価CLI・公式単価/データ条件・判断根拠を#14/PR #57に記録する。
実APIキーはローカルのみで使用し、証跡/PRへ含めない。
#18は本番adapter・usage/費用予約・moderationを接続し、#16の同意API、#37の公開受け入れを確認する。
今回のモデル選定を理由に本番Workerを有効化しない。
