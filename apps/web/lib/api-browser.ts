"use client";
import { createPreviewClient, ApiError } from "@alter/api-client/preview";
import { createApiClient } from "@alter/api-client";
import { accessMessage } from "./auth-policy";
const browserFetch: typeof fetch = async (input, init) => {
  const response = await fetch(input, { ...init, cache: "no-store" });
  // Full navigation intentionally destroys all user-specific in-memory state.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (response.status === 401) window.location.assign("/login?error=expired");
  return response;
};
export const browserApi = () => createPreviewClient("", browserFetch);
export const activityApi = () => createApiClient("", browserFetch);
export function operationMessage(cause: unknown) {
  if (!(cause instanceof ApiError))
    return "接続できませんでした。入力を保持しています。再試行してください。";
  if (
    [
      "INVITATION_REQUIRED",
      "ACCOUNT_INACTIVE",
      "AUTHORIZATION_UNAVAILABLE",
    ].includes(cause.detail.code)
  )
    return accessMessage(cause.detail.code);
  const messages: Record<number, string> = {
    401: "ログインの有効期限が切れました。",
    404: "対象が見つかりません。最新の状態を読み直してください。",
    409: "別の更新と競合しました。最新の人格を読み直してから再確認してください。",
    422: "入力内容を確認してください。文字数や項目数が上限を超えている可能性があります。",
    429: "利用回数の上限です。時間をおいて再試行するか、手動で入力してください。",
    503: "現在この機能を利用できません。手動入力で続けるか、時間をおいて再試行してください。",
  };
  return (
    messages[cause.status] ?? "保存できませんでした。もう一度お試しください。"
  );
}
