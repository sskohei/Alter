import type { components } from "@alter/api-client";
export type Conversation = components["schemas"]["ConversationSummary"];
export type Detail = components["schemas"]["ConversationDetail"];
export type Notification = components["schemas"]["Notification"];
export const conversationStatus: Record<Conversation["status"], string> = {
  queued: "開始待ち",
  running: "進行中",
  completed: "完了",
  failed: "生成失敗",
  cancelled: "停止済み",
};
export const isOngoing = (detail: Detail) =>
  detail.status === "running" || detail.status === "queued";
export function dateLabel(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat("ja-JP", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Asia/Tokyo",
      }).format(new Date(value))
    : "未開始";
}
export function costLabel(value: number | null | undefined) {
  return value == null
    ? "集計中"
    : new Intl.NumberFormat("ja-JP", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 6,
      }).format(value);
}
