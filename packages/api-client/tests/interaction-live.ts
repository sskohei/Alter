// Run by pytest against real local FastAPI and PostgreSQL, with synthetic external Auth.
import assert from "node:assert/strict";
import { createApiClient, authenticatedFetch, unwrap, ApiError } from "../src/index";
const [baseUrl, owner, outsider, ownId, otherId] = process.argv.slice(2);
const client = (sub: string) => createApiClient(baseUrl, authenticatedFetch({
  accessToken: async () => sub, refreshToken: async () => null, onUnauthenticated: () => {},
}));
const api = client(owner);
const params = {path: {id: ownId}};
const initial = await unwrap(await api.GET("/api/v1/agents/{id}/settings", {params}));
assert.equal(initial.autonomy_enabled, false);
assert.equal(initial.discord_publish, false);
const budget = await unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {
  frequency_per_day: 1, daily_budget_usd: 0.2, max_session_usd: 0.02,
}}));
assert.equal(budget.daily_budget_usd, 0.2);
assert.equal(budget.autonomy_enabled, false);
const on = await unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {autonomy_enabled: true}}));
assert.equal(on.autonomy_enabled, true);
assert.equal(on.discord_publish, false);
// The UI confirms Discord separately; this checks persistence of its independent flag.
await unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {discord_publish: true}}));
await assert.rejects(async () => unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {max_session_usd: 0.3}})), (error: unknown) => error instanceof ApiError && error.status === 422);
const unchanged = await unwrap(await api.GET("/api/v1/agents/{id}/settings", {params}));
assert.equal(unchanged.max_session_usd, 0.02);
const pair = {path: {id: ownId, other_id: otherId}};
for (let i = 0; i < 2; i++) {
  const result = await api.PUT("/api/v1/agents/{id}/blocks/{other_id}", {params: pair});
  await unwrap(result);
  assert.equal(result.response.status, 204);
}
const off = await unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {autonomy_enabled: false}}));
assert.equal(off.autonomy_enabled, false);
assert.equal(off.discord_publish, true);
for (let i = 0; i < 2; i++) await unwrap(await api.DELETE("/api/v1/agents/{id}/blocks/{other_id}", {params: pair}));
const revoked = await unwrap(await api.PATCH("/api/v1/agents/{id}/settings", {params, body: {discord_publish: false}}));
assert.equal(revoked.autonomy_enabled, false);
assert.equal(revoked.discord_publish, false);
await assert.rejects(async () => unwrap(await client(outsider).PATCH("/api/v1/agents/{id}/settings", {params, body: {autonomy_enabled: true}})), (error: unknown) => error instanceof ApiError && error.status === 404);
await assert.rejects(async () => unwrap(await client(outsider).PUT("/api/v1/agents/{id}/blocks/{other_id}", {params: pair})), (error: unknown) => error instanceof ApiError && error.status === 404);
console.log("Generated settings client → FastAPI → PostgreSQL: passed");
