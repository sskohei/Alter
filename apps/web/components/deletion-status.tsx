"use client";
import Link from "next/link";
import { useCallback } from "react";
import {
  createPreviewClient,
  unwrap,
  type components,
} from "@alter/api-client/preview";
import { useResource } from "../lib/use-resource";
import { dateLabel } from "../lib/activity";
import { DataState } from "./data-state";
import { Button } from "./ui/button";
type Deletion = components["schemas"]["DeletionRequest"];
const pending = (data: Deletion) =>
  data.status === "pending" || data.status === "running";
export function DeletionStatus() {
  const load = useCallback(
    (signal: AbortSignal) =>
      createPreviewClient("")
        .GET("/api/v1/users/me/deletion", { signal, cache: "no-store" })
        .then(unwrap),
    [],
  );
  const resource = useResource(load, 30_000, pending);
  const labels = {
    pending: "削除要求を受け付けました",
    running: "削除処理中です",
    completed: "削除処理が完了しました",
    failed: "削除処理の確認が必要です",
  };
  return (
    <main className="public-page">
      <h1>アカウント削除の状況</h1>
      <p>
        削除要求後は通常の画面を利用できません。このページでは本人の処理状況だけを確認します。
      </p>
      <DataState {...resource} retry={resource.refresh} />
      {resource.data && (
        <section className="card">
          <h2>{labels[resource.data.status]}</h2>
          <p>要求日時: {dateLabel(resource.data.requested_at)}</p>
          <p>
            完了日時:{" "}
            {resource.data.completed_at
              ? dateLabel(resource.data.completed_at)
              : "未完了"}
          </p>
          {resource.data.status !== "completed" && (
            <p>
              原則30日以内の削除を運営が追跡します。処理の失敗・遅延は私的な連絡先へお問い合わせください。
            </p>
          )}
          <Button variant="outline" onClick={resource.refresh}>
            状態を再確認する
          </Button>
        </section>
      )}
      <p>
        認証情報が削除された後は、招待時のsskoheiとの私的な連絡先で本人確認のうえ結果をお伝えします。公開Issueへ個人情報を書かないでください。
      </p>
      <Link href="/login">ログイン画面へ</Link>
    </main>
  );
}
