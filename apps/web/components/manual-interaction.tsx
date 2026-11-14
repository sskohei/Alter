"use client";
import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ApiError, unwrap } from "@alter/api-client";
import { activityApi } from "../lib/api-browser";
import { useResource } from "../lib/use-resource";
import { useRetryAfter } from "../lib/use-retry-after";
import { DataState } from "./data-state";
import { Button } from "./ui/button";

export function ManualInteraction() {
  const router = useRouter();
  const load = useCallback(
    (signal: AbortSignal) => activityApi().GET("/api/v1/conversations/start", { signal }).then(unwrap),
    [],
  );
  const resource = useResource(load);
  const key = useRef<string | null>(null);
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retry = useRetryAfter();

  async function start() {
    if (submitting.current || retry.seconds) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    key.current ??= crypto.randomUUID();
    try {
      const conversation = await unwrap(await activityApi().POST("/api/v1/conversations/start", {
        headers: { "Idempotency-Key": key.current },
      }));
      key.current = null;
      router.push(`/conversations/${conversation.id}`);
    } catch (cause) {
      retry.defer(cause);
      // A rejected request has no reservation. A lost response must reuse its key.
      if (cause instanceof ApiError && cause.status < 500) key.current = null;
      setError(cause instanceof ApiError && cause.status === 409
        ? "開始できる相手がいないか、予算が不足しています。双方の人格承認・交流ON・頻度が0でないこと・ブロック・進行中の会話・予算を確認してください。"
        : cause instanceof ApiError && cause.status === 403
          ? "手動での交流開始が無効になっています。画面を再読み込みしてください。"
          : "交流を開始できませんでした。接続とAPI・Workerの起動状態を確認して再試行してください。");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  if (!resource.data?.enabled) return <DataState {...resource} retry={resource.refresh} />;
  return (
    <section className="card stack">
      <h2>今すぐ交流する</h2>
      <p>あなたのAIと、交流を許可している相手のAIで会話を始めます。完了後は何度でも開始できます。デモの手動開始には1日の回数制限がありません。</p>
      <p className="help">双方の人格承認と交流ONが必要です。費用上限と同時1会話の制限は適用されます。</p>
      {error && <p className="notice" role="alert">{error}</p>}
      {retry.seconds > 0 && <p role="status">再試行まで{retry.seconds}秒お待ちください。</p>}
      <div className="actions">
        <Button onClick={() => void start()} disabled={busy || retry.seconds > 0}>
          {busy ? "交流を開始中…" : "交流を開始する"}
        </Button>
        <Link href="/settings/interaction">交流設定を確認する</Link>
      </div>
    </section>
  );
}
