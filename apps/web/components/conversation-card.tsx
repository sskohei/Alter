import Link from "next/link";
import {
  conversationStatus,
  dateLabel,
  costLabel,
  type Conversation,
} from "../lib/activity";
export function ConversationCard({
  conversation,
}: {
  conversation: Conversation;
}) {
  return (
    <article className="card conversation-card" data-status={conversation.status}>
      <span className="sticker" aria-hidden="true">
        {conversationStatus[conversation.status]}
      </span>
      <p className="eyebrow">
        AI分身どうしの交流 · {conversationStatus[conversation.status]}
      </p>
      <h2>
        <Link href={`/conversations/${conversation.id}`}>
          {conversation.partner.name}との交流
        </Link>
      </h2>
      <p>
        <time dateTime={conversation.started_at ?? undefined}>
          {dateLabel(conversation.started_at)}
        </time>{" "}
        · Web参加者限定
      </p>
      <p>
        {conversation.status === "failed"
          ? "会話の生成に失敗しました。保存できた発言だけを詳細で確認できます。"
          : (conversation.summary ?? "要約は会話の完了後に表示します。")}
      </p>
      <p className="help">使用量: {costLabel(conversation.cost_usd)}</p>
    </article>
  );
}
