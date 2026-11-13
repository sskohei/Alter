# 運用費の概算（USD/月）

確認日: 2026-10-27。価格は地域、税、席数、超過使用量、為替で変動する。デプロイ直前に再見積もりする。下表は**常時動作する有料構成**であり、無料Webを疑似アクセスで起こし続ける前提ではない。

## 固定費の前提と根拠

| 項目 | 10人/100人の仮定 | 1,000人の仮定 | 出典・制限 |
|---|---:|---:|---|
| Vercel Web | Pro 1有料席 $20 | $20 | [Vercel価格](https://vercel.com/pricing)。Hobby $0は非商用個人利用に限定。3有料席なら+$40 |
| Render API | `0.5c-512mb` $7 | `1c-2g` $25 | [Renderの構成例](https://render.com/articles/production-rails-hosting-guide)、[Compute plans](https://render.com/docs/compute-plans)。実負荷で選ぶ |
| Render Bot | `0.5c-512mb` $7 | $7 | 常時Gateway接続が必要。同上 |
| Render Worker | `0.5c-512mb` $7 | `1c-2g` $25 | 会話並列数で増設。同上 |
| Render Cron | 最低$1 | 最低$1 | [Cron料金](https://render.com/docs/cronjobs)。実稼働分が最低額超なら増加 |
| Supabase | Pro $25 | Pro $25以上 | [Supabase価格](https://supabase.com/pricing)。Freeは500MB・非稼働1週で休止、Proは8GB/100k MAU等 |
| Redis | $0 | $0 | DBジョブ採用。必要になった時に追加見積 |
| 固定費小計 | **$67** | **$103以上** | 帯域/ストレージ/税/追加席を除く |

10人と100人は同じ最小固定構成とする。100人ではDB容量、Worker待機時間、CPUを実測し、足りなければ1,000人欄の上位プランへ移る。Render Hobby workspaceは$0+computeの仮定。チーム向けRender Pro workspaceが必要なら[Render価格](https://render.com/pricing)の$25/月を追加する。Vercel 3有料席なら+$40/月。無料の試作はVercel Hobbyの用途制限、Render Worker無料枠なし、Render無料Webの15分アイドル停止、Supabase無料休止があるため本番MVPの推奨構成にはしない。[Render無料制約](https://render.com/docs/free)、[Vercel Hobby条件](https://vercel.com/docs/plans/hobby)。

## Claude直接API: 10人MVPのLLM使用量モデル（#14）

2026-11-13確認。購入済みClaude APIクレジットを利用する方針。
評価対象は`claude-haiku-5-5`、実評価後の採用判定は未完了。
[公式単価](https://platform.claude.com/docs/en/models/haiku-5-5/overview)は、
入力100K tokens以下で入力$0.10 / 出力$0.50（各100万tokens）。100K超は$0.50/$2.50。
本MVPは入力8192 / 出力512 tokens以下を呼出の実装条件とする（#18で接続）。
thinkingも出力に含め、キャッシュ割引/無料枠は計算に使わない。

仮定: 10人、1人1日1交流参加、2人/会話、30日で150会話。
8発言で各入力1500/出力200 tokens、将来の要約予備は入力2000/出力300。
合計入力14000 / 出力1900 tokensとし、追加20%予備費を加える。
MVPの引用記憶は追加LLM不要なので、要約分は保守的な予備。moderationの追加呼出は別途計上する。

| 項目 | 10人MVPの見積 |
|---|---:|
| 1会話・20%予備費込み | $0.002820 |
| 150会話/月 | $0.423000 |
| 1人1日2交流参加の場合、300会話/月 | $0.846000 |
| 固定費$67＋150会話の生成費 | 約$67.42/月 |

**実測・最大費用の保証ではない。** token数、thinking、moderation、人格提案、再試行、
税/為替により増減する。購入時$5は現在残高や有効期限を保証しない。

上限見積: 1体につき4発言＋要約予備1回、各回入力8192/出力512を仮定すると
$0.005380。初回＋最大3再試行を全て消費しても$0.021520/体/会話。
既存の$0.05/体/会話・$0.20/体/日の枠内だが、判定用moderation/人格提案などは別途予約する。
10人が1日2回参加なら10会話/日で、双方を合わせた上記上限シナリオは$0.430400/日。
20%追加でも$0.516480/日。既存の運営上限は今回変更せず、#18/#19で全呼出を事前予約し停止する。

評価CLIは既定$0.10/回、最大85呼出、HTTP自動再試行なし。
[評価手順](../evaluations/llm/README.md)と[選定・データ条件](26-llm-candidate-review.md)を参照。
大人数向けLLM試算は今回の採用条件にしない。上のインフラ拡張欄は将来参考値に留める。
