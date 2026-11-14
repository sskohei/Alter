"use client";
import { ApiError } from "@alter/api-client/preview";
import { accessMessage } from "../lib/auth-policy";
import { Button } from "./ui/button";
export function dataErrorMessage(error: unknown) {
  if (!(error instanceof ApiError))
    return "接続できませんでした。時間をおいて再試行してください。";
  if (
    [
      "INVITATION_REQUIRED",
      "ACCOUNT_INACTIVE",
      "AUTHORIZATION_UNAVAILABLE",
    ].includes(error.detail.code)
  )
    return accessMessage(error.detail.code);
  const messages: Record<number, string> = {
    401: "ログインの有効期限が切れました。もう一度ログインしてください。",
    403: "このデータを操作する権限がありません。",
    404: "対象が見つかりません。削除済み、または閲覧できないデータです。",
    409: "別の更新と競合しました。最新の状態を読み直してください。",
    422: "入力内容を確認してください。",
    429: "リクエストが多いため待機しています。時間をおいて再試行してください。",
    503: "現在この機能を利用できません。時間をおいて再試行してください。",
  };
  return (
    messages[error.status] ?? "操作を完了できませんでした。再試行してください。"
  );
}
export function DataState({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error: unknown;
  retry: () => void;
}) {
  if (error)
    return (
      <section className="notice" role="alert">
        <p>{dataErrorMessage(error)}</p>
        <Button variant="outline" onClick={retry}>
          再試行
        </Button>
      </section>
    );
  return loading ? (
    <div className="card animate-pulse" role="status">
      読み込み中…
    </div>
  ) : null;
}
export function PageControls({
  hasPrevious,
  nextCursor,
  previous,
  next,
  disabled = false,
}: {
  hasPrevious: boolean;
  nextCursor?: string | null;
  previous: () => void;
  next: (cursor: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="actions" aria-label="ページ切替">
      <Button
        variant="outline"
        disabled={disabled || !hasPrevious}
        onClick={previous}
      >
        前のページ
      </Button>
      <Button
        variant="outline"
        disabled={disabled || !nextCursor}
        onClick={() => nextCursor && next(nextCursor)}
      >
        次のページ
      </Button>
    </div>
  );
}
