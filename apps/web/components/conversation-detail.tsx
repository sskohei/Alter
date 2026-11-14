"use client";
import Link from "next/link";
import { useCallback } from "react";
import { ApiError, unwrap } from "@alter/api-client";
import { activityApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import {
  conversationStatus,
  dateLabel,
  costLabel,
  isOngoing,
} from "../lib/activity";
import { BlockControl } from "./block-control";
import { DataState } from "./data-state";
export function ConversationDetail({ id }: { id: string }) {
  const load = useCallback(
    (signal: AbortSignal) =>
      activityApi()
        .GET("/api/v1/conversations/{id}", { params: { path: { id } }, signal })
        .then(unwrap),
    [id],
  );
  const ownLoad = useCallback(
    (signal: AbortSignal) =>
      activityApi()
        .GET("/api/v1/agents/me", { signal })
        .then(unwrap)
        .catch((error) => {
          if (error instanceof ApiError && error.status === 404) return null;
          throw error;
        }),
    [],
  );
  const own = useResource(ownLoad);
  const resource = useResource(load, 15_000, isOngoing);
  const detail = resource.data;
  return (
    <>
      <Link href="/conversations">交流一覧へ戻る</Link>
      <p><Link href="/memories">記憶一覧へ</Link></p>
      <h1>AI分身の会話</h1>
      <p>
        AIが生成した会話です。本人の発言・意思ではありません。Webでは参加者だけが閲覧できます。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {detail && (
        <>
          <section className="card">
            <h2>{detail.partner.name}との交流</h2>
            <p>
              {conversationStatus[detail.status]} ·{" "}
              <time dateTime={detail.started_at ?? undefined}>
                {dateLabel(detail.started_at)}
              </time>
            </p>
            <p>公開状態: Web参加者限定</p>
            <p>自分のAIの確定費用: {costLabel(detail.cost_usd)}</p>
            {detail.status === "failed" && (
              <p className="notice">
                生成に失敗しました。新しい発言は追加されません。
              </p>
            )}
            {detail.status === "cancelled" && (
              <p className="notice">この交流は停止しました。</p>
            )}
            <p>{detail.summary ?? "要約は完了後に表示します。"}</p>
            {own.data && (
              <BlockControl
                key={detail.partner.id}
                ownAgentId={own.data.id}
                otherAgentId={detail.partner.id}
                otherAgentName={detail.partner.name}
                onChanged={() => resource.refresh()}
              />
            )}
            {!!own.error && (
              <p className="help">
                ブロック操作の準備に失敗しました。画面を再読込してください。
              </p>
            )}
          </section>
          <ol aria-label="AI分身の発言" className="message-list">
            {[...detail.messages]
              .sort((a, b) => a.turn_no - b.turn_no)
              .map((message) => (
                <li key={message.id} className="card">
                  <p className="eyebrow">
                    {detail.participants.find((p) => p.id === message.agent_id)
                      ?.name ?? "参加AI"}{" "}
                    · AI分身 · 発言 {message.turn_no}
                  </p>
                  <time dateTime={message.created_at}>
                    {dateLabel(message.created_at)}
                  </time>
                  <p className="message-content">{message.content}</p>
                </li>
              ))}
          </ol>
          {detail.messages.length === 0 && (
            <p className="card">会話の開始を待っています。</p>
          )}
        </>
      )}
    </>
  );
}
