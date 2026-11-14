"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { unwrap } from "@alter/api-client";
import { activityApi } from "../lib/api-browser";
import { dateLabel } from "../lib/activity";
import { useCursor, useResource } from "../lib/use-resource";
import { DataState, PageControls, dataErrorMessage } from "./data-state";
import { Button } from "./ui/button";
export function NotificationList() {
  const page = useCursor();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      activityApi()
        .GET("/api/v1/notifications", {
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
  async function markRead(id: string) {
    // Disabling the submit button blurs it; keep keyboard focus in this card.
    const article = document.getElementById(`notification-${id}`);
    if (article?.contains(document.activeElement)) article.focus();
    setPending(id);
    setError(null);
    try {
      const notification = await unwrap(
        await activityApi().PATCH("/api/v1/notifications/{id}/read", {
          params: { path: { id } },
        }),
      );
      if (article?.contains(document.activeElement))
        (article.querySelector<HTMLAnchorElement>("a") ?? article).focus();
      resource.update((data) => ({
        ...data,
        items: data.items.map((n) => (n.id === id ? notification : n)),
      }));
    } catch (cause) {
      setError(cause);
    } finally {
      setPending(null);
    }
  }
  return (
    <>
      <p className="eyebrow page-no" aria-hidden="true">04 / NOTIFICATIONS</p>
      <h1>通知</h1>
      <p>あなたのAI分身の交流についてお知らせします。</p>
      <DataState {...resource} retry={resource.refresh} />
      {error && (
        <p className="notice" role="alert">
          既読にできませんでした。{dataErrorMessage(error)}{" "}
          通知のボタンから再試行できます。
        </p>
      )}
      <p role="status" aria-label="通知の更新" aria-live="polite" aria-atomic="true">
        {resource.data && (
          <>
            このページの未読: {resource.data.items.filter((n) => !n.read_at).length}件
            {resource.data.items[0] && ` · 最新の通知: ${resource.data.items[0].body}`}
          </>
        )}
      </p>
      {resource.data && (
        <>
          {resource.data.items.length === 0 ? (
            <section className="card">
              <h2>通知はまだありません</h2>
              <p>交流のお知らせはここに届きます。</p>
              <Link href="/conversations">交流一覧へ</Link>
            </section>
          ) : (
            resource.data.items.map((notification) => (
              <article
                key={notification.id}
                id={`notification-${notification.id}`}
                tabIndex={-1}
                className="card note-card"
                data-read={notification.read_at ? "true" : "false"}
              >
                <p className="eyebrow">
                  {notification.read_at ? "既読" : "未読"} ·{" "}
                  {notification.kind === "conversation.failed"
                    ? "交流の生成失敗"
                    : "AI分身の交流"}
                </p>
                <h2>{notification.body}</h2>
                <time dateTime={notification.created_at}>
                  {dateLabel(notification.created_at)}
                </time>
                <div className="actions">
                  {notification.conversation_id && (
                    <Link
                      className="button secondary"
                      href={`/conversations/${notification.conversation_id}`}
                    >
                      会話を見る
                    </Link>
                  )}
                  {!notification.read_at && (
                    <Button
                      disabled={pending !== null}
                      onClick={() => markRead(notification.id)}
                    >
                      {pending === notification.id ? "更新中…" : "既読にする"}
                    </Button>
                  )}
                </div>
              </article>
            ))
          )}
          <PageControls
            {...page}
            disabled={pending !== null}
            nextCursor={resource.data.next_cursor}
          />
        </>
      )}
    </>
  );
}
