"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import { ApiError, unwrap, type components } from "@alter/api-client/preview";
import { browserApi } from "../lib/api-browser";
import { useCursor, useResource } from "../lib/use-resource";
import { dateLabel } from "../lib/activity";
import { DataState, PageControls, dataErrorMessage } from "./data-state";
import { Button } from "./ui/button";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogCancel,
  AlertDialogAction,
} from "./ui/alert-dialog";
type Memory = components["schemas"]["Memory"];
export function MemoryList() {
  const page = useCursor();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
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
      const memories = await unwrap(
        await api.GET("/api/v1/memories", {
          params: {
            query: {
              agent_id: agent.id,
              limit: 10,
              ...(page.cursor ? { cursor: page.cursor } : {}),
            },
          },
          signal,
        }),
      );
      return { agent, memories };
    },
    [page.cursor],
  );
  const resource = useResource(load);
  async function remove(memory: Memory) {
    const snapshot = resource.data;
    if (!snapshot) return;
    setPending(memory.id);
    setError(null);
    setMessage("");
    resource.update((data) =>
      data
        ? {
            ...data,
            memories: {
              ...data.memories,
              items: data.memories.items.filter((m) => m.id !== memory.id),
            },
          }
        : data,
    );
    try {
      const result = await browserApi().DELETE("/api/v1/memories/{id}", {
        params: { path: { id: memory.id } },
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      if (!result.response.ok)
        throw new ApiError(result.response.status, {
          code: "DELETE_FAILED",
          message: "Could not delete memory",
        });
      setMessage("記憶を削除しました。");
    } catch (cause) {
      if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
        if (cause.status !== 404)
          resource.update((data) =>
            data
              ? { ...data, memories: { ...data.memories, items: [] } }
              : data,
          );
        resource.refresh();
      } else resource.update(() => snapshot);
      setError(cause);
    } finally {
      setPending(null);
    }
  }
  return (
    <>
      <p className="eyebrow page-no" aria-hidden="true">06 / MEMORIES</p>
      <h1>AI分身の記憶</h1>
      <p>
        会話から抽出した記憶候補です。本人の確定した事実とは限りません。出典と内容を確認し、不要な候補を削除できます。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {message && <p role="status">{message}</p>}
      {pending && <p role="status">記憶を削除中…</p>}
      {!!error && (
        <p className="notice" role="alert">
          記憶を削除できませんでした。{dataErrorMessage(error)}{" "}
          表示を再確認してから再試行してください。
        </p>
      )}
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
          {resource.data.memories.items.length === 0 ? (
            <section className="card">
              <h2>記憶はまだありません</h2>
              <p>交流が完了すると、出典付きの記憶候補がここに表示されます。</p>
              <Link href="/conversations">交流の記録を見る</Link>
            </section>
          ) : (
            <div className="memo-grid">
              {resource.data.memories.items.map((memory) => (
                <MemoryCard
                  key={memory.id}
                  memory={memory}
                  busy={pending !== null}
                  remove={() => void remove(memory)}
                />
              ))}
            </div>
          )}
          <PageControls
            {...page}
            disabled={pending !== null}
            nextCursor={resource.data.memories.next_cursor}
          />
        </>
      )}
    </>
  );
}
function MemoryCard({
  memory,
  busy,
  remove,
}: {
  memory: Memory;
  busy: boolean;
  remove: () => void;
}) {
  return (
    <article className="card memo" data-importance={memory.importance}>
      <span className="memo-dots" aria-hidden="true">
        {"●".repeat(memory.importance)}
        {"○".repeat(Math.max(0, 5 - memory.importance))}
      </span>
      <p className="eyebrow">記憶候補 · 重要度 {memory.importance} / 5</p>
      <h2 className="text-base">{memory.text}</h2>
      <p>
        作成日:{" "}
        <time dateTime={memory.created_at}>{dateLabel(memory.created_at)}</time>
      </p>
      <div className="actions">
        {memory.source_conversation_id ? (
          <Link
            className="button secondary"
            href={`/conversations/${memory.source_conversation_id}`}
          >
            出典の会話を見る
          </Link>
        ) : (
          <p className="help">出典の会話は削除済み、または参照できません。</p>
        )}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="outline" disabled={busy}>
              この記憶を削除する
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogTitle className="text-xl font-bold">
              この記憶を削除しますか？
            </AlertDialogTitle>
            <AlertDialogDescription>
              この候補を削除して、今後の交流で使う記憶から外します。元に戻せません。出典の会話は保持されます。
            </AlertDialogDescription>
            <p>{memory.text}</p>
            <div className="actions">
              <AlertDialogCancel asChild>
                <Button variant="outline">キャンセル</Button>
              </AlertDialogCancel>
              <AlertDialogAction asChild>
                <Button
                  onClick={(event) => {
                    event.preventDefault();
                    remove();
                  }}
                >
                  記憶の削除を確定する
                </Button>
              </AlertDialogAction>
            </div>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </article>
  );
}
