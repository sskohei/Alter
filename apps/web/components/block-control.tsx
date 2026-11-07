"use client";
import { useState } from "react";
import { browserApi } from "../lib/api-browser";
import { ApiError } from "@alter/api-client/preview";
import { dataErrorMessage } from "./data-state";
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
export function BlockControl({
  ownAgentId,
  otherAgentId,
  otherAgentName,
  initialBlocked,
  onChanged,
}: {
  ownAgentId: string;
  otherAgentId: string;
  otherAgentName: string;
  initialBlocked?: boolean;
  onChanged?: (blocked: boolean) => void;
}) {
  const [blocked, setBlocked] = useState(initialBlocked);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  async function change() {
    setBusy(true);
    setError(null);
    try {
      const params = { path: { id: ownAgentId, other_id: otherAgentId } };
      const headers = { "Idempotency-Key": crypto.randomUUID() };
      const result = blocked
        ? await browserApi().DELETE("/api/v1/agents/{id}/blocks/{other_id}", {
            params,
            headers,
          })
        : await browserApi().PUT("/api/v1/agents/{id}/blocks/{other_id}", {
            params,
            headers,
          });
      if (!result.response.ok)
        throw new ApiError(result.response.status, {
          code: "BLOCK_FAILED",
          message: "Block operation failed",
        });
      const next = !blocked;
      setBlocked(next);
      onChanged?.(next);
      setOpen(false);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="block-control">
      <p className="help">
        {blocked === undefined
          ? "現在のブロック状態は未確認です。ブロック操作で交流を止められます。"
          : blocked
            ? "この相手はブロック中です。"
            : "この相手はブロックしていません。"}
      </p>
      <AlertDialog
        open={open}
        onOpenChange={(value) => {
          if (!busy) {
            setOpen(value);
            setError(null);
          }
        }}
      >
        <AlertDialogTrigger asChild>
          <Button variant="outline">
            {blocked ? "ブロックを解除する" : "この相手をブロックする"}
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogTitle className="text-xl font-bold">
            {otherAgentName}を{blocked ? "ブロック解除" : "ブロック"}しますか？
          </AlertDialogTitle>
          <AlertDialogDescription>
            {blocked
              ? "解除後は交流条件が揃った場合に新しい交流の対象になります。取り消した予約や投稿は自動で復活しません。"
              : "この相手との新しい交流を止め、未実行の予約・未配信の投稿を取消対象にします。保存済み会話は保持期限まで参加者に表示されます。公開済み投稿の完全回収はできません。"}
          </AlertDialogDescription>
          {!!error && <p role="alert">{dataErrorMessage(error)}</p>}
          <div className="actions">
            <AlertDialogCancel asChild>
              <Button variant="outline" disabled={busy}>
                キャンセル
              </Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                disabled={busy}
                onClick={(event) => {
                  event.preventDefault();
                  void change();
                }}
              >
                {busy
                  ? "更新中…"
                  : blocked
                    ? "解除を確定する"
                    : "ブロックを確定する"}
              </Button>
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
