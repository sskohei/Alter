// @vitest-environment node
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../app/auth/discord/callback/route";
import { discordStateCookie, stateHash } from "../lib/discord-policy";

const auth = vi.hoisted(() => ({
  getUser: vi.fn(), getSession: vi.fn(), refreshSession: vi.fn(),
}));
const cookie = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }));
vi.mock("../lib/supabase/server", () => ({ serverSupabase: async () => ({ auth }) }));
vi.mock("next/headers", () => ({ cookies: async () => cookie }));
const state = "synthetic-state-0000000000000001";
const pending = (userId = "owner", expires = Date.now() + 600_000) => JSON.stringify({ userId, hash: stateHash(state), expires });
let upstream: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://web.example.test");
  vi.stubEnv("API_URL", "https://api.example.test");
  auth.getUser.mockResolvedValue({ data: { user: { id: "owner" } }, error: null });
  auth.getSession.mockResolvedValue({ data: { session: { access_token: "server-only-bearer" } } });
  cookie.get.mockReturnValue({ value: pending() });
  upstream = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ connected: true, discord_user_id: "123" }));
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const callback = (query = `code=server-only-code&state=${state}`) => GET(new NextRequest(`https://web.example.test/auth/discord/callback?${query}`));

test("callback forwards code/state with the verified user's Bearer only to the formal API", async () => {
  const response = await callback();
  expect(auth.getUser).toHaveBeenCalledOnce();
  expect(upstream).toHaveBeenCalledOnce();
  const request = upstream.mock.calls[0][0] as Request;
  expect(request.url).toBe("https://api.example.test/api/v1/discord/link/complete");
  expect(request.method).toBe("POST");
  expect(request.headers.get("Authorization")).toBe("Bearer server-only-bearer");
  expect(request.cache).toBe("no-store");
  expect(await request.json()).toEqual({ code: "server-only-code", state });
  expect(cookie.delete).toHaveBeenCalledWith(discordStateCookie);
  expect(response.headers.get("location")).toBe("https://web.example.test/settings/discord?linked=1");
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
  expect(JSON.stringify([...response.headers]) + await response.text()).not.toMatch(/server-only|synthetic-state/);
});
for (const [status, error] of [[400, "state"], [409, "conflict"], [503, "unavailable"]] as const) {
  test(`formal API ${status} becomes a safe reason code`, async () => {
    upstream.mockResolvedValue(Response.json({ error: { code: "CONFLICT", message: "private provider detail" } }, { status }));
    expect((await callback()).headers.get("location")).toBe(`https://web.example.test/settings/discord?error=${error}`);
    expect(cookie.delete).toHaveBeenCalledOnce();
  });
}
for (const mode of ["missing", "expired", "owner", "state", "unauthenticated", "auth-error"] as const) {
  test(`callback rejects ${mode} before code exchange`, async () => {
    if (mode === "missing") cookie.get.mockReturnValue(undefined);
    if (mode === "expired") cookie.get.mockReturnValue({ value: pending("owner", Date.now() - 1) });
    if (mode === "owner") cookie.get.mockReturnValue({ value: pending("another-user") });
    if (mode === "state") cookie.get.mockReturnValue({ value: pending().replace(stateHash(state), stateHash("wrong-state")) });
    if (mode === "unauthenticated") auth.getUser.mockResolvedValue({ data: { user: null }, error: null });
    if (mode === "auth-error") auth.getUser.mockResolvedValue({ data: { user: { id: "owner" } }, error: new Error("Auth failed") });
    expect((await callback()).headers.get("location")).toContain("error=state");
    expect(upstream).not.toHaveBeenCalled();
    expect(cookie.delete).toHaveBeenCalledOnce();
  });
}
for (const query of ["error=access_denied", "code=one", `code=one&code=two&state=${state}`, `code=one&state=${state}&state=two`]) {
  test(`callback rejects cancel or malformed query: ${query}`, async () => {
    expect((await callback(query)).headers.get("location")).toContain(query.startsWith("error=") ? "error=cancel" : "error=state");
    expect(upstream).not.toHaveBeenCalled();
    expect(cookie.delete).toHaveBeenCalledOnce();
  });
}
