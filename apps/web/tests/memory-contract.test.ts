// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/openapi.json";
import { createApiClient, unwrap } from "@alter/api-client";
import { createMockFetch } from "@alter/api-client/mock";
import { relationshipLabel } from "../lib/relationships";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "preview");
function validate(name: string, value: unknown) {
  const check = ajv.compile({ $ref: `preview#/components/schemas/${name}` });
  expect(check(value), JSON.stringify(check.errors)).toBe(true);
}
test("memory deletion persists, retains cursor coverage, and blocks are reflected in relationships", async () => {
  const client = createApiClient(
    "http://mock.test",
    createMockFetch("normal", { seedAgent: true, activity: true }),
  );
  client.use({
    onRequest: ({ request }) => {
      request.headers.set("Authorization", "Bearer synthetic");
      return request;
    },
  });
  const own = await unwrap(await client.GET("/api/v1/agents/me"));
  const query = { agent_id: own.id, limit: 10 };
  const first = await unwrap(
    await client.GET("/api/v1/memories", { params: { query } }),
  );
  validate("Page_Memory_", first);
  expect(first.items).toHaveLength(10);
  const deleted = first.items.at(-1)!.id;
  expect(
    (
      await client.DELETE("/api/v1/memories/{id}", {
        params: { path: { id: deleted } },
      })
    ).response.status,
  ).toBe(204);
  const next = await unwrap(
    await client.GET("/api/v1/memories", {
      params: { query: { ...query, cursor: first.next_cursor! } },
    }),
  );
  validate("Page_Memory_", next);
  expect(next.items).toHaveLength(2);
  expect(next.items[0].id).not.toBe(first.items[8].id);
  const reload = await unwrap(
    await client.GET("/api/v1/memories", { params: { query } }),
  );
  expect(reload.items.some((m) => m.id === deleted)).toBe(false);
  expect(
    (
      await client.GET("/api/v1/memories", {
        params: { query: { agent_id: "10000000-0000-4000-8000-000000000099" } },
      })
    ).response.status,
  ).toBe(404);
  expect(
    (
      await client.DELETE("/api/v1/memories/{id}", {
        params: { path: { id: "60000000-0000-4000-8000-000000000099" } },
      })
    ).response.status,
  ).toBe(404);
  const relations = await unwrap(await client.GET("/api/v1/relationships"));
  validate("Page_Relationship_", relations);
  const params = {
    path: { id: own.id, other_id: relations.items[0].other_agent_id },
  };
  await client.PUT("/api/v1/agents/{id}/blocks/{other_id}", { params });
  expect(
    (await unwrap(await client.GET("/api/v1/relationships"))).items[0].blocked,
  ).toBe(true);
  await client.DELETE("/api/v1/agents/{id}/blocks/{other_id}", { params });
  expect(
    (await unwrap(await client.GET("/api/v1/relationships"))).items[0].blocked,
  ).toBe(false);
});
for (const [count, label] of [
  [0, "初対面"],
  [1, "顔見知り"],
  [2, "顔見知り"],
  [3, "よく交流する相手"],
  [10, "よく交流する相手"],
] as const)
  test(`relationship label at ${count}`, () =>
    expect(relationshipLabel(count)).toBe(label));
