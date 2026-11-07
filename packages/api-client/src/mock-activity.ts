import type { components } from "./preview-schema";
type Summary = components["schemas"]["ConversationSummary"];
type Detail = components["schemas"]["ConversationDetail"];
type Notification = components["schemas"]["Notification"];
export const mockOwnAgentId = "10000000-0000-4000-8000-000000000001";
export const mockPartnerId = "20000000-0000-4000-8000-000000000001";
export const mockConversationId = "30000000-0000-4000-8000-000000000001";
const instant = "2026-11-07T00:00:00Z";
export const activityConversations: Summary[] = Array.from(
  { length: 12 },
  (_, i) => ({
    id: `30000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
    status:
      i === 1
        ? "running"
        : i === 2
          ? "failed"
          : i === 3
            ? "cancelled"
            : i === 4
              ? "queued"
              : "completed",
    partner: { id: mockPartnerId, name: "あおい" },
    started_at: i === 4 ? null : instant,
    summary:
      i === 0
        ? "読書や散歩について語り合いました。"
        : i === 1
          ? null
          : `交流の要約 ${i + 1}`,
    visibility: "participants",
    cost_usd: i === 4 ? 0 : 0.002,
  }),
);

// Cursors point into the stable source array, so deletion never shifts a page.
export function createMockPager() {
  const cursors = new Map<string, number>();
  const tokens = new Map<number, string>();
  return <T>(
    items: readonly T[],
    url: URL,
    include: (item: T) => boolean = () => true,
  ) => {
    const cursor = url.searchParams.get("cursor");
    const start = cursor ? cursors.get(cursor) : 0;
    const limit = Number(url.searchParams.get("limit") ?? 20);
    if (
      start === undefined ||
      !Number.isInteger(limit) ||
      limit < 1 ||
      limit > 50
    )
      return null;
    const positions = items
      .map((item, index) => ({ item, index }))
      .filter((row) => row.index >= start && include(row.item));
    const rows = positions.slice(0, limit);
    const next = (rows.at(-1)?.index ?? start) + 1;
    if (positions.length > limit && !tokens.has(next)) {
      const token = crypto.randomUUID();
      tokens.set(next, token);
      cursors.set(token, next);
    }
    return {
      items: rows.map((row) => row.item),
      next_cursor: positions.length > limit ? tokens.get(next)! : null,
    };
  };
}
export function conversationFixture(
  summary: Summary,
  ownName = "こはる",
): Detail {
  return {
    ...summary,
    participants: [{ id: mockOwnAgentId, name: ownName }, summary.partner],
    messages:
      summary.status === "queued"
        ? []
        : [
            {
              id: "40000000-0000-4000-8000-000000000001",
              agent_id: mockOwnAgentId,
              turn_no: 1,
              content: "最近読んだ本の話をしませんか。",
              created_at: instant,
            },
            {
              id: "40000000-0000-4000-8000-000000000002",
              agent_id: mockPartnerId,
              turn_no: 2,
              content: "いいですね。散歩の途中で本屋に寄るのが好きです。",
              created_at: instant,
            },
          ],
  };
}
export function createActivityMock(empty: boolean) {
  const conversations = empty
    ? []
    : activityConversations.map((c) => ({ ...c }));
  let notifications: Notification[] = conversations.map((c, i) => ({
    id: `50000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
    kind:
      c.status === "failed" ? "conversation_failed" : "conversation_completed",
    body: `AI分身の交流のお知らせ ${i + 1}`,
    created_at: instant,
    read_at: null,
    conversation_id: c.id,
  }));
  const pages = createMockPager();
  return async (request: Request): Promise<Response | null> => {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path === "/api/v1/discord/connection" && request.method === "GET")
      return Response.json({ connected: false, discord_user_id: null });
    if (path === "/api/v1/conversations" && request.method === "GET") {
      const page = pages(conversations, url);
      return page ? Response.json(page) : new Response(null, { status: 422 });
    }
    if (path.startsWith("/api/v1/conversations/") && request.method === "GET") {
      const summary = conversations.find(
        (c) => c.id === path.split("/").at(-1),
      );
      return summary
        ? Response.json(conversationFixture(summary))
        : new Response(null, { status: 404 });
    }
    if (path === "/api/v1/notifications" && request.method === "GET") {
      const page = pages(
        notifications,
        url,
        (n) => url.searchParams.get("unread") !== "true" || !n.read_at,
      );
      return page ? Response.json(page) : new Response(null, { status: 422 });
    }
    const match = path.match(/^\/api\/v1\/notifications\/([^/]+)\/read$/);
    if (match && request.method === "PATCH") {
      const notification = notifications.find((n) => n.id === match[1]);
      if (!notification) return new Response(null, { status: 404 });
      const updated = { ...notification, read_at: instant };
      notifications = notifications.map((n) =>
        n.id === updated.id ? updated : n,
      );
      return Response.json(updated);
    }
    return null;
  };
}
