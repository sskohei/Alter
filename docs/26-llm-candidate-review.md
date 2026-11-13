# Claude直接APIによるモデル選定（#14）

2026-11-13。ユーザーが購入済みのClaude APIクレジット（購入時$5）を使い、
招待制10人MVPに絞る方針を承認した。残高・有効期限は未確認。
OpenRouter経由と複数社比較は採用しない。11/11のカタログは過去の調査資料として残す。

## 決定と未完了条件

供給元はAnthropic直接API、評価するモデルは`claude-haiku-5-5`。
[公式モデル仕様](https://platform.claude.com/docs/en/models/haiku-5-5/overview)を11/13に確認。
adaptive thinking / effort low、max_tokens 512（thinking込み）、キャッシュ/外部ツールなしで開始する。
温度等は送らない。`Utterance`のJSON Schemaを使用し、ローカルで元の制約を再検証する。
拒否・打切り時も課金され得るため、結果を採用せず停止する。
[構造化出力](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

**供給元の決定と実モデルの採用判定は分ける。** 実API評価と日本語・安全性レビューは未完了。
APIキー設定後に[評価手順](../evaluations/llm/README.md)で合成5ケース＋10組の会話を実行する。
合格条件は全安全ケース合格、schema成功100%、日本語各項目4/5以上。
評価CLIは本番Workerへ接続せず、#18のliveゲートを解除しない。

Haikuを第一候補とする理由は、短い日本語会話のMVPで必要な品質を低費用で検証するため。
他社モデルは購入済みクレジットを活用する方針により比較対象外。
上位ClaudeモデルはHaikuの品質不足が確認されてから検討する。

## 単価と10人MVPの見積

[公式単価](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、2026-11-13確認。
入力100K tokens以下: 入力$0.10 / 出力$0.50（各100万tokens）。100K超では$0.50/$2.50。
評価は入力8192以下、出力512以下に制限し、thinkingも出力usageで計上する。
通常見積は[費用表](20-cost-estimation.md)。キャッシュ割引や無料枠は予算に織り込まない。
モデルの更新/価格変更時は同じ条件で再評価する。

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

## 状態

実評価: 未実施（APIキー未設定）。日本語レビュー: 未実施。
#14は実評価・採点・モデル採用記録が揃うまでopenを維持する。
