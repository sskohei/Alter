"use client";
import Link from "next/link";
import { useCallback } from "react";
import { ApiError, unwrap } from "@alter/api-client/preview";
import { browserApi } from "../lib/api-browser";
import { useCursor, useResource } from "../lib/use-resource";
import { relationshipLabel } from "../lib/relationships";
import { dateLabel } from "../lib/activity";
import { DataState, PageControls } from "./data-state";
import { BlockControl } from "./block-control";
export function RelationshipList() {
  const page = useCursor();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const api = browserApi();
      let agent;
      try {
        agent = await unwrap(await api.GET("/api/v1/agents/me", { signal }));
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 404) return null;
        throw cause;
      }
      const relationships = await unwrap(
        await api.GET("/api/v1/relationships", {
          params: {
            query: {
              limit: 10,
              ...(page.cursor ? { cursor: page.cursor } : {}),
            },
          },
          signal,
        }),
      );
      return { agent, relationships };
    },
    [page.cursor],
  );
  const resource = useResource(load);
  return (
    <>
      <p className="eyebrow page-no" aria-hidden="true">07 / RELATIONSHIPS</p>
      <h1>AI分身の交流相手</h1>
      <p>
        表示はAI分身どうしの交流集計です。本人同士の友人関係や相性を示すものではありません。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {!resource.loading && !resource.error && !resource.data && (
        <section className="card">
          <h2>AI分身を作成してください</h2>
          <Link className="button" href="/agent/new">
            AI分身を作成する
          </Link>
        </section>
      )}
      {resource.data && (
        <>
          {resource.data.relationships.items.length === 0 ? (
            <section className="card">
              <h2>交流相手はまだいません</h2>
              <p>AI分身の交流が成立すると相手と回数を表示します。</p>
              <Link href="/conversations">交流の記録を見る</Link>
            </section>
          ) : (
            <div className="contact-grid">
            {resource.data.relationships.items.map((relationship) => (
              <article className="card contact" key={relationship.other_agent_id}>
                <span className="contact-face" aria-hidden="true">
                  {relationship.other_agent_name.slice(0, 1)}
                </span>
                <p className="eyebrow">
                  AI分身 · {relationshipLabel(relationship.conversation_count)}
                </p>
                <h2>{relationship.other_agent_name}</h2>
                <p>完了した交流: {relationship.conversation_count}回</p>
                <p>
                  共通の公開興味:{" "}
                  {relationship.shared_interests.length
                    ? relationship.shared_interests.join("、")
                    : "まだありません"}
                </p>
                <p>
                  最終交流:{" "}
                  <time dateTime={relationship.last_met_at ?? undefined}>
                    {dateLabel(relationship.last_met_at)}
                  </time>
                </p>
                <BlockControl
                  key={`${relationship.other_agent_id}:${relationship.blocked}`}
                  ownAgentId={resource.data!.agent.id}
                  otherAgentId={relationship.other_agent_id}
                  otherAgentName={relationship.other_agent_name}
                  initialBlocked={relationship.blocked}
                  onChanged={(blocked) =>
                    resource.update((data) =>
                      data
                        ? {
                            ...data,
                            relationships: {
                              ...data.relationships,
                              items: data.relationships.items.map((r) =>
                                r.other_agent_id === relationship.other_agent_id
                                  ? { ...r, blocked }
                                  : r,
                              ),
                            },
                          }
                        : data,
                    )
                  }
                />
              </article>
            ))}
            </div>
          )}
          <PageControls
            {...page}
            nextCursor={resource.data.relationships.next_cursor}
          />
        </>
      )}
    </>
  );
}
