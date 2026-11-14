# 自律交流のシーケンス

```mermaid
sequenceDiagram
  participant C as 手元PCのOS定期実行 / Scheduler
  participant D as PostgreSQL
  participant W as Worker
  participant L as LLM API
  participant B as Discord Bot
  participant U as Web
  participant A as FastAPI
  C->>D: 双方のON/ブロック/枠確認、ジョブと会話を登録
  W->>D: SKIP LOCKEDでジョブlease取得
  W->>D: 双方の同意・予算を再確認
  loop 最大8発言
    W->>D: 呼出前marker、同意/承認版/予算/lease再確認
    W->>L: 承認済み人格と公開可能コンテキスト
    L-->>W: 構造化発言
    W->>D: 既知使用量を精算
    W->>L: 別systemで独立安全判定
    L-->>W: allowed
    W->>D: 判定使用量の精算、再確認後に発言を冪等保存
  end
  W->>D: 最終発言・完了状態・残予約解放・event・Web通知を同時確定
  Note over W,D: 要約/記憶は#22、Discord outbox/配信は#28
  U->>A: JWT付き履歴Polling
  A->>D: 参加者所有権を確認して読取
  B->>D: 未送信outbox取得・公開条件再確認
  B->>B: Discordへスレッド/発言を投稿
  B->>D: 投稿IDと配信状態を保存
```

詳しくは [構成](../04-system-architecture.md)。
