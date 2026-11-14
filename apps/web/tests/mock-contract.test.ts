// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import liveSchema from "../../../packages/api-client/openapi.json";
import schema from "../../../packages/api-client/preview.openapi.json";
import { createMockFetch, scenarios } from "@alter/api-client/mock";

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "preview");
ajv.addSchema(liveSchema, "live");
for (const scenario of scenarios) {
  test(`mock response validates against generated OpenAPI: ${scenario}`, async () => {
    const response = await createMockFetch(scenario)("https://example.test/api/v1/users/me", { headers: { Authorization: "Bearer synthetic" } });
    const name = response.ok ? "Profile" : "ErrorResponse";
    const validate = ajv.compile({ $ref: `preview#/components/schemas/${name}` });
    const body = await response.json();
    expect(validate(body), JSON.stringify(validate.errors)).toBe(true);
    const validateLive = ajv.compile({ $ref: `live#/components/schemas/${name}` });
    expect(validateLive(body), JSON.stringify(validateLive.errors)).toBe(true);
  });
}

test("live and preview share exact user operations and schemas", () => {
  expect(schema.paths["/api/v1/users/me"]).toEqual(liveSchema.paths["/api/v1/users/me"]);
  for (const name of ["Profile", "ProfilePatch", "ErrorResponse"] as const) {
    expect(schema.components.schemas[name]).toEqual(liveSchema.components.schemas[name]);
  }
  expect(liveSchema.paths).not.toHaveProperty("/__mock/profiles");
});

test("PATCH validates generated live constraints, normalizes, and isolates state", async () => {
  const mock = createMockFetch();
  const call = (body: unknown) => mock("https://example.test/api/v1/users/me", { method: "PATCH", headers: { Authorization: "Bearer synthetic" }, body: JSON.stringify(body) });
  for (const body of [{}, null, { display_name: " " }, { display_name: "x".repeat(81) }, { display_name: "x\u0000y" }, { display_name: "ok", owner_id: "other" }, { display_name: 1 }]) {
    expect((await call(body)).status).toBe(422);
  }
  const good = await call({ display_name: ` ${"🙂".repeat(80)} ` });
  expect(good.status).toBe(200);
  expect((await good.json()).display_name).toBe("🙂".repeat(80));
  const original = await createMockFetch()("https://example.test/api/v1/users/me", { headers: { Authorization: "Bearer synthetic" } });
  expect((await original.json()).display_name).toBe("招待ユーザー");
  expect((await mock("https://example.test/api/v1/users/me", { method: "PATCH", headers: { Authorization: "Bearer synthetic" }, body: "{" })).status).toBe(422);
});

test("generated pagination contract supports normal/empty pages and rejects unknown cursors", async () => {
  const { createPreviewClient, unwrap } = await import("@alter/api-client/preview");
  const validate = ajv.compile({ $ref: "preview#/components/schemas/ProfilePage" });
  for (const scenario of ["normal", "empty"] as const) {
    const client = createPreviewClient("https://example.test", createMockFetch(scenario));
    const headers = { Authorization: "Bearer synthetic" };
    const first = await client.GET("/__mock/profiles", { headers, params: { query: { limit: 2 } } }).then(unwrap);
    expect(validate(first), JSON.stringify(validate.errors)).toBe(true);
    expect(first.items).toHaveLength(scenario === "normal" ? 2 : 0);
    if (first.next_cursor) {
      const second = await client.GET("/__mock/profiles", { headers, params: { query: { limit: 2, cursor: first.next_cursor } } }).then(unwrap);
      expect(validate(second), JSON.stringify(validate.errors)).toBe(true);
      expect(second.items).toHaveLength(1);
      expect(second.next_cursor).toBeNull();
      expect(second.items[0].id).not.toBe(first.items[0].id);
    }
    const invalid = await client.GET("/__mock/profiles", { headers, params: { query: { cursor: "unknown" } } });
    expect(invalid.response.status).toBe(422);
  }
});
