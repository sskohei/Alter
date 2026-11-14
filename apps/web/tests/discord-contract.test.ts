// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/openapi.json";
import { createApiClient, unwrap } from "@alter/api-client";
import { createMockFetch } from "@alter/api-client/mock";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "live");
function validate(name: string, value: unknown) {
  const check = ajv.compile({ $ref: `live#/components/schemas/${name}` });
  expect(check(value), JSON.stringify(check.errors)).toBe(true);
}
function client() {
  const api = createApiClient("https://mock.test", createMockFetch("normal", { activity: true, seedAgent: true }));
  api.use({ onRequest: ({ request }) => { request.headers.set("Authorization", "Bearer synthetic"); return request; } });
  return api;
}
async function start(api: ReturnType<typeof client>) {
  const result = await unwrap(await api.POST("/api/v1/discord/link/start"));
  validate("DiscordLinkStart", result);
  return new URL(result.url).searchParams.get("state")!;
}
test("formal Discord fixtures preserve independent publication/DM choices through link and unlink", async () => {
  const api = client();
  const before = await unwrap(await api.GET("/api/v1/discord/guilds"));
  before.forEach((guild) => validate("Guild", guild));
  const state = await start(api);
  const linked = await unwrap(await api.POST("/api/v1/discord/link/complete", { body: { state, code: "synthetic-code" } }));
  validate("DiscordConnection", linked);
  expect(linked.connected).toBe(true);
  expect(await unwrap(await api.GET("/api/v1/discord/guilds"))).toEqual(before);
  const agent = await unwrap(await api.GET("/api/v1/agents/me"));
  expect((await unwrap(await api.GET("/api/v1/agents/{id}/settings", { params: { path: { id: agent.id } } }))).discord_publish).toBe(false);
  expect((await unwrap(await api.GET("/api/v1/users/me"))).discord_dm_notifications).toBe(false);
  expect((await api.DELETE("/api/v1/discord/connection")).response.status).toBe(204);
  expect((await unwrap(await api.GET("/api/v1/discord/connection"))).connected).toBe(false);
  expect((await api.DELETE("/api/v1/discord/connection")).response.status).toBe(204);
});
for (const code of ["synthetic-code", "expired", "conflict"]) {
  test(`state is consumed after ${code} and cannot be replayed`, async () => {
    const api = client();
    const state = await start(api);
    await api.POST("/api/v1/discord/link/complete", { body: { state, code } });
    const replay = await api.POST("/api/v1/discord/link/complete", { body: { state, code: "synthetic-code" } });
    expect(replay.response.status).toBe(400);
    validate("ErrorResponse", replay.error);
  });
}
test("unlink invalidates outstanding states; another user's factory cannot consume them", async () => {
  const owner = client();
  const state = await start(owner);
  const other = await client().POST("/api/v1/discord/link/complete", { body: { state, code: "synthetic-code" } });
  expect(other.response.status).toBe(400);
  await owner.DELETE("/api/v1/discord/connection");
  expect((await owner.POST("/api/v1/discord/link/complete", { body: { state, code: "synthetic-code" } })).response.status).toBe(400);
});
