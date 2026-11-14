// Real local FastAPI/PostgreSQL. Only external Auth is replaced by the Python fixture.
import assert from "node:assert/strict";
import { createApiClient, authenticatedFetch, unwrap } from "../src/index";
const [baseUrl, owner, partner, conversation] = process.argv.slice(2);
const client = (sub: string, fresh = false) => createApiClient(baseUrl, authenticatedFetch({
  accessToken: async () => `${fresh ? "fresh" : "stale"}:${sub}`,
  refreshToken: async () => null,
  onUnauthenticated: () => {},
}));
const api = client(owner, true);
const other = client(partner);
const preferences = await unwrap(await api.GET("/api/v1/preferences"));
assert.equal(preferences.web_notifications, true);
assert.equal(preferences.discord_dm_notifications, false);
await unwrap(await api.PATCH("/api/v1/preferences", { body: { web_notifications: false } }));
assert.equal((await unwrap(await api.GET("/api/v1/preferences"))).web_notifications, false);
await unwrap(await api.PATCH("/api/v1/users/me", { body: { display_name: "新しい表示名" } }));
assert.equal((await unwrap(await api.GET("/api/v1/users/me"))).display_name, "新しい表示名");
assert.equal((await client(owner).POST("/api/v1/users/me/deletion", { body: { confirm: true } })).response.status, 403);
assert.equal((await other.GET("/api/v1/conversations/{id}", { params: { path: { id: conversation } } })).response.status, 200);
const accepted = await api.POST("/api/v1/users/me/deletion", { body: { confirm: true }, headers: { "Idempotency-Key": "account-live" } });
assert.equal(accepted.response.status, 202);
assert.equal(accepted.response.headers.get("Cache-Control"), "no-store");
const deletion = await unwrap(accepted);
assert.equal(deletion.status, "pending");
const replay = await unwrap(await client(owner).POST("/api/v1/users/me/deletion", { body: { confirm: true }, headers: { "Idempotency-Key": "account-live" } }));
assert.equal(replay.id, deletion.id);
assert.equal((await api.GET("/api/v1/users/me")).response.status, 403);
const status = await unwrap(await client(owner).GET("/api/v1/users/me/deletion"));
assert.equal(status.id, deletion.id);
assert.deepEqual(Object.keys(status).sort(), ["completed_at", "id", "requested_at", "status"]);
assert.equal((await other.GET("/api/v1/users/me/deletion")).response.status, 404);
assert.equal((await other.GET("/api/v1/conversations/{id}", { params: { path: { id: conversation } } })).response.status, 404);
console.log("Generated account client → FastAPI → PostgreSQL: passed");
