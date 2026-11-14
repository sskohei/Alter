// Real local FastAPI/PostgreSQL; external Auth is replaced only by the pytest fixture.
import assert from "node:assert/strict";
import { createApiClient, authenticatedFetch, unwrap, ApiError } from "../src/index";
const [baseUrl, owner, outsider, agent, otherAgent, memory] = process.argv.slice(2);
const client = (sub: string) => createApiClient(baseUrl, authenticatedFetch({
  accessToken: async () => sub, refreshToken: async () => null, onUnauthenticated: () => {},
}));
const api = client(owner);
const params = {query: {agent_id: agent}};
const first = await unwrap(await api.GET("/api/v1/memories", {params}));
assert.equal(first.items.length, 3);
assert.ok(first.items.some(m => m.source_conversation_id === null));
assert.equal((await unwrap(await api.GET("/api/v1/relationships"))).items[0].conversation_count, 1);
for (let i = 0; i < 2; i++) {
  const result = await api.DELETE("/api/v1/memories/{id}", {params: {path: {id: memory}}});
  await unwrap(result);
  assert.equal(result.response.status, 204);
}
assert.equal((await unwrap(await api.GET("/api/v1/memories", {params}))).items.length, 1);
const pair = {path: {id: agent, other_id: otherAgent}};
await unwrap(await api.PUT("/api/v1/agents/{id}/blocks/{other_id}", {params: pair}));
assert.equal((await unwrap(await api.GET("/api/v1/relationships"))).items[0].blocked, true);
await unwrap(await api.DELETE("/api/v1/agents/{id}/blocks/{other_id}", {params: pair}));
assert.equal((await unwrap(await api.GET("/api/v1/relationships"))).items[0].blocked, false);
await assert.rejects(async () => unwrap(await client(outsider).GET("/api/v1/memories", {params})), (error: unknown) => error instanceof ApiError && error.status === 404);
await assert.rejects(async () => unwrap(await client(outsider).DELETE("/api/v1/memories/{id}", {params: {path: {id: memory}}})), (error: unknown) => error instanceof ApiError && error.status === 404);
console.log("Generated memory client → FastAPI → PostgreSQL: passed");
