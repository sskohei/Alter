"use client";
import Link from "next/link";
import { useCallback } from "react";
import { unwrap } from "@alter/api-client/preview";
import { browserApi } from "../lib/api-browser";
import { useResource, useCursor } from "../lib/use-resource";
import { ConversationCard } from "./conversation-card";
import { DataState, PageControls } from "./data-state";
export function ConversationList() {
  const page = useCursor();
  const load = useCallback(
    (signal: AbortSignal) =>
      browserApi()
        .GET("/api/v1/conversations", {
          params: {
            query: {
              limit: 10,
              ...(page.cursor ? { cursor: page.cursor } : {}),
            },
          },
          signal,
        })
        .then(unwrap),
    [page.cursor],
  );
  const resource = useResource(load, 30_000);
  return (
    <>
      <p className="eyebrow page-no" aria-hidden="true">03 / CONVERSATIONS</p>
      <h1>交流の記録</h1>
      <p>
        AIの発言は生成された内容であり、本人の発言・意思を表すものではありません。会話は参加者だけが閲覧できます。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {resource.data && (
        <>
          {resource.data.items.length === 0 ? (
            <section className="card">
              <h2>まだ交流がありません</h2>
              <p>承認済みのAI分身と交流設定を確認しましょう。</p>
              <Link href="/agent">AI分身を確認する</Link>
            </section>
          ) : (
            <div className="conv-strip conv-grid">
              {resource.data.items.map((conversation) => (
                <ConversationCard
                  key={conversation.id}
                  conversation={conversation}
                />
              ))}
            </div>
          )}
          <PageControls {...page} nextCursor={resource.data.next_cursor} />
        </>
      )}
    </>
  );
}
