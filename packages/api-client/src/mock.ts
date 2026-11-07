import type { components } from "./preview-schema";

export const scenarios = ["normal", "empty", "401", "403", "inactive", "404", "409", "422", "429", "503"] as const;
export type Scenario = typeof scenarios[number];
export const profile: components["schemas"]["Profile"] = {
  id: "00000000-0000-4000-8000-000000000001", display_name: "招待ユーザー",
  created_at: "2026-11-07T00:00:00Z", web_notifications: true,
  discord_dm_notifications: false, onboarded_at: null,
};
export const errorCodes: Record<number, string> = {401: "UNAUTHENTICATED", 403: "INVITATION_REQUIRED", 404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "AUTHORIZATION_UNAVAILABLE"};
export function mockError(status: number, code = errorCodes[status]) {
  return Response.json({ error: { code, message: "Mock: 操作を完了できませんでした。", request_id: crypto.randomUUID() } }, { status });
}

// Each factory owns its state. Tests/users never share profiles across factories.
export function createMockFetch(scenario: Scenario = "normal"): typeof fetch {
  let current = { ...profile };
  return async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.pathname === "/health") return Response.json({ status: "ok", service: "api", mode: "mock" });
    if (!request.headers.get("Authorization")?.startsWith("Bearer ")) return mockError(401);
    if (scenario === "inactive") return mockError(403, "ACCOUNT_INACTIVE");
    if (!Number.isNaN(Number(scenario))) return mockError(Number(scenario));
    if (url.pathname === "/api/v1/users/me") {
      if (request.method === "PATCH") {
        const body = await request.json();
        if (typeof body.display_name !== "string" || !body.display_name.trim() || body.display_name.length > 200) return mockError(422);
        current = { ...current, display_name: body.display_name };
      }
      return Response.json(current);
    }
    return mockError(404);
  };
}

export const pageFixture = (scenario: "normal" | "empty") => ({ items: scenario === "empty" ? [] : [{ ...profile }], next_cursor: null });
