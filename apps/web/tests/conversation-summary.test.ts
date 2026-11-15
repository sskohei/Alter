// @vitest-environment node
import { expect, test } from "vitest";
import { needsConversationRefresh, type Detail } from "../lib/activity";

test("a just-completed conversation keeps refreshing until its asynchronous summary arrives", () => {
  const now = Date.parse("2026-11-15T01:00:00Z");
  const detail = { status: "completed", summary: null, started_at: "2026-11-15T00:59:00Z" } as Detail;
  expect(needsConversationRefresh(detail, now)).toBe(true);
  expect(needsConversationRefresh({ ...detail, summary: "読書の楽しみを共有した。" }, now)).toBe(false);
  expect(needsConversationRefresh(detail, now + 10 * 60_000)).toBe(false);
  expect(needsConversationRefresh({ ...detail, status: "failed" }, now)).toBe(false);
  expect(needsConversationRefresh({ ...detail, status: "running" }, now)).toBe(true);
});
