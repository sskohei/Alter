// Executed by the DB integration suite against a real local FastAPI server.
import assert from "node:assert/strict";
import { createApiClient, authenticatedFetch, unwrap, ApiError } from "../src/index";
const [baseUrl, owner, outsider, done, running, others] = process.argv.slice(2);
const clientFor = (sub: string) => createApiClient(baseUrl, authenticatedFetch({
  accessToken: async () => sub,
  refreshToken: async () => null,
  onUnauthenticated: () => {},
}));
const api = clientFor(owner);
const first = await unwrap(await api.GET("/api/v1/conversations", {params: {query: {limit: 1}}}));
assert.equal(first.items[0].id, running);
assert.equal(first.items[0].cost_usd, null);
assert.ok(first.next_cursor);
const next = await unwrap(await api.GET("/api/v1/conversations", {params: {query: {limit: 1, cursor: first.next_cursor}}}));
assert.equal(next.items[0].id, done);
assert.equal(next.items[0].cost_usd, 0.03);
assert.equal(next.next_cursor, null);
const detail = await unwrap(await api.GET("/api/v1/conversations/{id}", {params: {path: {id: done}}}));
assert.deepEqual(detail.messages.map(m => m.turn_no), [1, 3]);
assert.equal(detail.partner.name, "BのAI");
assert.equal(detail.visibility, "participants");
await assert.rejects(async () => unwrap(await api.GET("/api/v1/conversations/{id}", {params: {path: {id: others}}})), (error: unknown) => error instanceof ApiError && error.status === 404);
const notes = await unwrap(await api.GET("/api/v1/notifications", {params: {query: {unread: true}}}));
assert.equal(notes.items.length, 1);
assert.equal(notes.items[0].conversation_id, done);
const id = notes.items[0].id;
const read = await unwrap(await api.PATCH("/api/v1/notifications/{id}/read", {params: {path: {id}}}));
assert.ok(read.read_at);
const again = await unwrap(await api.PATCH("/api/v1/notifications/{id}/read", {params: {path: {id}}}));
assert.equal(again.read_at, read.read_at);
assert.equal((await unwrap(await api.GET("/api/v1/notifications", {params: {query: {unread: true}}}))).items.length, 0);
const other = clientFor(outsider);
await assert.rejects(async () => unwrap(await other.GET("/api/v1/conversations/{id}", {params: {path: {id: done}}})), (error: unknown) => error instanceof ApiError && error.status === 404);
await assert.rejects(async () => unwrap(await other.PATCH("/api/v1/notifications/{id}/read", {params: {path: {id}}})), (error: unknown) => error instanceof ApiError && error.status === 404);
console.log("Generated activity client → FastAPI → PostgreSQL: passed");
