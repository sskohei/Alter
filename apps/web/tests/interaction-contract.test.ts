// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/preview.openapi.json";
import { createMockFetch } from "@alter/api-client/mock";
import { createPreviewClient, unwrap } from "@alter/api-client/preview";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "preview");
test("settings preserve independent consents, reject invalid budgets and unknown owner", async () => {
  const client = createPreviewClient(
    "http://mock.test",
    createMockFetch("normal", { seedAgent: true, activity: true }),
  );
  client.use({
    onRequest: ({ request }) => {
      request.headers.set("Authorization", "Bearer synthetic");
      return request;
    },
  });
  const agent = await unwrap(await client.GET("/api/v1/agents/me"));
  const params = { path: { id: agent.id } };
  const original = await unwrap(
    await client.GET("/api/v1/agents/{id}/settings", { params }),
  );
  const check = ajv.compile({
    $ref: "preview#/components/schemas/AgentSettings",
  });
  expect(check(original)).toBe(true);
  expect(original.autonomy_enabled).toBe(false);
  expect(original.discord_publish).toBe(false);
  const on = await unwrap(
    await client.PATCH("/api/v1/agents/{id}/settings", {
      params,
      body: { autonomy_enabled: true },
    }),
  );
  expect(on.autonomy_enabled).toBe(true);
  expect(on.discord_publish).toBe(false);
  const off = await unwrap(
    await client.PATCH("/api/v1/agents/{id}/settings", {
      params,
      body: { autonomy_enabled: false },
    }),
  );
  expect(off.autonomy_enabled).toBe(false);
  expect(
    (
      await client.PATCH("/api/v1/agents/{id}/settings", {
        params,
        body: { daily_budget_usd: -1 },
      })
    ).response.status,
  ).toBe(422);
  expect(
    (
      await client.PATCH("/api/v1/agents/{id}/settings", {
        params: { path: { id: "10000000-0000-4000-8000-000000000099" } },
        body: { autonomy_enabled: true },
      })
    ).response.status,
  ).toBe(404);
  const guilds = await unwrap(await client.GET("/api/v1/discord/guilds"));
  const validateGuild = ajv.compile({
    $ref: "preview#/components/schemas/Guild",
  });
  expect(guilds.every((g) => validateGuild(g))).toBe(true);
});
