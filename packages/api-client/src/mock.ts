import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../openapi.json";
import type { components } from "./schema";
import type { components as Preview } from "./preview-schema";

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "live");
const validPatch = ajv.compile({ $ref: "live#/components/schemas/ProfilePatch" });

export const scenarios = ["normal", "empty", "401", "403", "inactive", "404", "409", "422", "429", "503"] as const;
export type Scenario = typeof scenarios[number];
export const profile: components["schemas"]["Profile"] = {
  id: "00000000-0000-4000-8000-000000000001", display_name: "招待ユーザー",
  created_at: "2026-11-07T00:00:00Z", web_notifications: true,
  discord_dm_notifications: false, onboarded_at: null,
};
export const errorCodes: Record<number, components["schemas"]["ErrorBody"]["code"]> = {401: "UNAUTHENTICATED", 403: "INVITATION_REQUIRED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "AUTHORIZATION_UNAVAILABLE"};
export function mockError(status: number, code = errorCodes[status]) {
  const requestId = crypto.randomUUID();
  return Response.json({ error: { code, message: "Mock: 操作を完了できませんでした。", request_id: requestId } } satisfies components["schemas"]["ErrorResponse"], {
    status, headers: { "X-Request-ID": requestId, "Cache-Control": "no-store", ...(status === 429 ? { "Retry-After": "60" } : {}) },
  });
}
function success(body: unknown) {
  return Response.json(body, { headers: { "X-Request-ID": crypto.randomUUID(), "Cache-Control": "no-store" } });
}

// Each factory owns its state. This synthetic mock never verifies real JWTs.
export function createMockFetch(scenario: Scenario = "normal"): typeof fetch {
  let current = { ...profile };
  return async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.pathname === "/health") return request.method === "GET" ? success({ status: "ok", service: "api", mode: "mock" }) : mockError(405);
    if (!["/api/v1/users/me", "/__mock/profiles"].includes(url.pathname)) return mockError(404);
    if (!(url.pathname === "/api/v1/users/me" ? ["GET", "PATCH"] : ["GET"]).includes(request.method)) return mockError(405);
    if (!/^Bearer \S+$/i.test(request.headers.get("Authorization") ?? "")) return mockError(401);
    if (scenario === "inactive") return mockError(403, "ACCOUNT_INACTIVE");
    if (!Number.isNaN(Number(scenario))) return mockError(Number(scenario));
    if (url.pathname === "/__mock/profiles") {
      const limitText = url.searchParams.get("limit") ?? "20";
      const limit = Number(limitText);
      const cursor = url.searchParams.get("cursor");
      const items = pageFixture(scenario as "normal" | "empty").items;
      const offset = cursor === null ? 0 : items.findIndex(item => `after:${item.id}` === cursor) + 1;
      if (!/^\d+$/.test(limitText) || !Number.isInteger(limit) || limit < 1 || limit > 50 || (cursor !== null && offset === 0)) return mockError(422);
      const page = items.slice(offset, offset + limit);
      return success({ items: page, next_cursor: offset + limit < items.length ? `after:${page.at(-1)!.id}` : null } satisfies Preview["schemas"]["ProfilePage"]);
    }
    if (request.method === "PATCH") {
      let body: unknown;
      try { body = await request.json(); } catch { return mockError(422); }
      // Pydantic normalizes before applying its generated length constraints.
      if (typeof body === "object" && body !== null && "display_name" in body && typeof body.display_name === "string") {
        const normalized = body.display_name.trim();
        if (/[\u0000-\u001f\u007f-\u009f]/u.test(normalized)) return mockError(422);
        body.display_name = normalized;
      }
      if (!validPatch(body)) return mockError(422);
      current = { ...current, display_name: (body as components["schemas"]["ProfilePatch"]).display_name };
    }
    return success(current);
  };
}

// This is a mock-only collection, never a production user directory.
export const pageFixture = (scenario: "normal" | "empty"): Preview["schemas"]["ProfilePage"] => ({
  items: scenario === "empty" ? [] : Array.from({ length: 3 }, (_, i) => ({ ...profile, id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`, display_name: `合成ユーザー${i + 1}` })),
  next_cursor: null,
});
