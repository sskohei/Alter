import type { components } from "./preview-schema";
import {
  createMockPager,
  mockOwnAgentId,
  mockConversationId,
} from "./mock-activity";
type Memory = components["schemas"]["Memory"];
type Relationship = components["schemas"]["Relationship"];
export const memoryFixtures: Memory[] = Array.from({ length: 12 }, (_, i) => ({
  id: `60000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
  text: `記憶候補 ${i + 1}: 本や散歩について会話したという記録。`,
  importance: i === 0 ? 5 : (i % 5) + 1,
  source_conversation_id:
    i === 1
      ? null
      : i === 2
        ? "30000000-0000-4000-8000-000000000099"
        : mockConversationId,
  created_at: "2026-11-07T00:00:00Z",
  status: "active",
}));
export const relationshipFixtures: Relationship[] = Array.from(
  { length: 12 },
  (_, i) => ({
    other_agent_id: `20000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
    other_agent_name: i === 0 ? "あおい" : `交流相手 ${i + 1}`,
    conversation_count: i === 0 ? 0 : i === 1 ? 2 : i + 1,
    shared_interests: ["読書", "散歩"],
    last_met_at: i === 0 ? null : "2026-11-07T00:00:00Z",
    blocked: false,
  }),
);
export function createMemoryMock(empty: boolean, blocked: Set<string>) {
  const memories = empty ? [] : memoryFixtures.map((m) => ({ ...m }));
  const relationships = empty
    ? []
    : relationshipFixtures.map((r) => ({ ...r }));
  const deleted = new Set<string>();
  const pages = createMockPager();
  return async (request: Request): Promise<Response | null> => {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path === "/api/v1/memories" && request.method === "GET") {
      if (url.searchParams.get("agent_id") !== mockOwnAgentId)
        return new Response(null, { status: 404 });
      const page = pages(memories, url, (memory) => !deleted.has(memory.id));
      return page ? Response.json(page) : new Response(null, { status: 422 });
    }
    if (path.startsWith("/api/v1/memories/") && request.method === "DELETE") {
      const id = path.split("/").at(-1)!;
      if (!memories.some((m) => m.id === id) || deleted.has(id))
        return new Response(null, { status: 404 });
      deleted.add(id);
      return new Response(null, { status: 204 });
    }
    if (path === "/api/v1/relationships" && request.method === "GET") {
      const rows = relationships.map((r) => ({
        ...r,
        blocked: blocked.has(r.other_agent_id),
      }));
      const page = pages(rows, url);
      return page ? Response.json(page) : new Response(null, { status: 422 });
    }
    return null;
  };
}
