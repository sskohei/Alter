// @vitest-environment node
import { expect, test, vi } from "vitest";
import { authenticatedFetch, createPreviewClient, unwrap, ApiError, pagination } from "@alter/api-client/preview";
import { createMockFetch, scenarios, pageFixture } from "@alter/api-client/mock";

test("401 refresh preserves the request body without sending a client request ID", async () => {
  const requests: Request[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const req = new Request(input, init); requests.push(req);
    return requests.length === 1 ? new Response(null, { status: 401 }) : Response.json({ ok: true });
  };
  const refreshToken = vi.fn(async () => "new");
  const transport = authenticatedFetch({ accessToken: async () => "old", refreshToken, onUnauthenticated: vi.fn(), fetch: fetcher });
  await transport("https://example.test/api", { method: "POST", body: '{"version":1}', headers: { "X-Request-ID": "untrusted-client-id" } });
  expect(requests).toHaveLength(2);
  expect(await requests[0].text()).toBe(await requests[1].text());
  expect(requests[1].headers.get("Authorization")).toBe("Bearer new");
  expect(requests[0].headers.has("X-Request-ID")).toBe(false);
  expect(requests[1].headers.has("X-Request-ID")).toBe(false);
  expect(requests[1].cache).toBe("no-store");
  expect(refreshToken).toHaveBeenCalledOnce();
});

test("persistent 401 stops after a single retry", async () => {
  const fetcher = vi.fn(async () => new Response(null, { status: 401 }));
  const logout = vi.fn();
  await authenticatedFetch({ accessToken: async () => "old", refreshToken: async () => "new", onUnauthenticated: logout, fetch: fetcher })("https://example.test");
  expect(fetcher).toHaveBeenCalledTimes(2); expect(logout).toHaveBeenCalledOnce();
});

for (const scenario of scenarios.filter(s => !["empty", "inactive"].includes(s))) {
  test(`generated client + mock: ${scenario}`, async () => {
    const client = createPreviewClient("https://example.test", authenticatedFetch({ accessToken: async () => "synthetic", refreshToken: async () => null, onUnauthenticated: () => {}, fetch: createMockFetch(scenario) }));
    const result = client.GET("/api/v1/users/me").then(unwrap);
    if (scenario === "normal") expect((await result).display_name).toBe("招待ユーザー");
    else await expect(result).rejects.toMatchObject({ status: Number(scenario), detail: { request_id: expect.any(String) } });
  });
}
test("pagination keeps opaque cursors and bounds page size", () => {
  expect(pagination("opaque+/=", 100)).toEqual({ cursor: "opaque+/=", limit: 50 });
  expect(pagination(null, 0)).toEqual({ limit: 1 });
  expect(new ApiError(409, { code: "CONFLICT", message: "再読込" }).message).toBe("再読込");
});

test("normal and empty page fixtures share the same pagination envelope", () => {
  expect(pageFixture("empty")).toEqual({items: [], next_cursor: null});
  expect(pageFixture("normal").items).toHaveLength(1);
});


test("malformed upstream errors use the shared service-unavailable code", async () => {
  await expect(unwrap({ response: new Response(null, { status: 503 }) }))
    .rejects.toMatchObject({ status: 503, detail: { code: "SERVICE_UNAVAILABLE" } });
});
