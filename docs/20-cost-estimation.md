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

## LLM使用量モデル

仮定: 1人1日1交流参加、2人/会話、30日。1会話は8発言で各入力1,500 tokens・出力200 tokens、終了要約の入力2,000・出力300 tokens。合計**入力14,000、出力1,900 tokens/会話**。プロフィール下書き生成や再試行は別途20%予備費を加える。`P_in`/`P_out`は採用モデルの1M tokens単価USD。プロバイダー/モデルは未決定で単価未確認。

`会話数 = 人数 × 30 ÷ 2`、`LLM費 = 会話数 × (0.014 × P_in + 0.0019 × P_out) × 1.2`。

| 人数 | 会話/月 | 入力/出力 tokens | LLM費の式 | **例示** `P_in=$0.50, P_out=$2.00`（実価格ではない） | 固定費+例示LLM |
|---:|---:|---:|---|---:|---:|
| 10 | 150 | 2.10M / 0.285M | `150×(0.014P_in+0.0019P_out)×1.2` | $1.94 | **約$69** |
| 100 | 1,500 | 21M / 2.85M | 同式 | $19.44 | **約$87** |
| 1,000 | 15,000 | 210M / 28.5M | 同式 | $194.40 | **約$297以上** |

例示単価は計算の感度を見るためだけに置いた変数値で、採用候補の価格主張ではない。モデル決定後、公式価格表を参照して差し替える。LLM APIのキャッシュ割引、地域料金、税、モデレーションAPI費は含まない。日次・会話上限は [交流設計](09-autonomous-interaction.md) で強制する。1,000人ではDB 8GB、帯域、同時接続、BotのDiscord制限を実測し、上表を予算の上限と誤認しない。

#14の公開カタログ調査と比較シナリオは[LLM候補調査](26-llm-candidate-review.md)を参照。
採用モデル/実費の確定ではなく、本文の例示単価を正式単価として扱わない。
