// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/openapi.json";
import { createMockFetch } from "@alter/api-client/mock";
import { createApiClient, unwrap } from "@alter/api-client";
const ajv = new Ajv2020({ strict: false }); addFormats(ajv); ajv.addSchema(schema, "live");
test("account fixtures match the shared contract and deletion exposes only minimal progress", async () => {
  const client = createApiClient("http://mock.test", createMockFetch()); client.use({ onRequest: ({ request }) => { request.headers.set("Authorization", "Bearer synthetic"); return request; } });
  const preferences = await unwrap(await client.GET("/api/v1/preferences")); expect(ajv.validate({ $ref: "live#/components/schemas/Preferences" }, preferences)).toBe(true);
  const changed = await unwrap(await client.PATCH("/api/v1/preferences", { body: { discord_dm_notifications: true } })); expect(changed.discord_dm_notifications).toBe(true);
  const deletion = await unwrap(await client.POST("/api/v1/users/me/deletion", { body: { confirm: true } })); expect(ajv.validate({ $ref: "live#/components/schemas/DeletionRequest" }, deletion)).toBe(true);
  expect((await client.GET("/api/v1/users/me")).response.status).toBe(403);
  const status = await unwrap(await client.GET("/api/v1/users/me/deletion")); expect(status.status).toBe("running"); expect(Object.keys(status).sort()).toEqual(["completed_at", "id", "requested_at", "status"]);
});
