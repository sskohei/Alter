// @vitest-environment node
import { expect, test } from "vitest";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../../packages/api-client/preview.openapi.json";
import {
  createPreviewClient,
  unwrap,
  type components,
} from "@alter/api-client/preview";
import { createMockFetch } from "@alter/api-client/mock";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema(schema, "preview");
const body: components["schemas"]["AgentDraftInput"] = {
  name: "分身",
  personality: "穏やか",
  values_text: "誠実",
  interests: ["本"],
  speaking_style: "丁寧",
  interaction_policy: "相手を尊重",
  shareable_info: "好きな本",
  restrictions: "なし",
  private_info: "PRIVATE_SENTINEL",
};
function validate(name: string, value: unknown) {
  const check = ajv.compile({ $ref: `preview#/components/schemas/${name}` });
  expect(check(value), JSON.stringify(check.errors)).toBe(true);
}
test("generated input and all lifecycle responses match OpenAPI; approval leaves autonomy off", async () => {
  const client = createPreviewClient("http://mock.test", createMockFetch());
  client.use({
    onRequest: ({ request }) => {
      request.headers.set("Authorization", "Bearer synthetic");
      return request;
    },
  });
  validate("AgentDraftInput", body);
  const agent = await unwrap(await client.POST("/api/v1/agents", { body }));
  validate("Agent", agent);
  expect(JSON.stringify(agent)).not.toContain("PRIVATE_SENTINEL");
  const params = { path: { id: agent.id } };
  const suggestion = await client.POST("/api/v1/agents/{id}/profile/suggest", {
    params, body: { answers: { personality: "穏やか" } },
  });
  expect(suggestion.response.status).toBe(503);
  validate("ErrorResponse", suggestion.error);
  expect(suggestion.error?.error.code).toBe("SERVICE_UNAVAILABLE");
  const first = await unwrap(
    await client.GET("/api/v1/agents/{id}/profile", { params }),
  );
  validate("ProfileRevision", first);
  expect(first.approved_at).toBeNull();
  expect(JSON.stringify(first)).not.toContain("PRIVATE_SENTINEL");
  const approved = await unwrap(
    await client.POST("/api/v1/agents/{id}/profile/approve", {
      params,
      body: { version: first.version },
    }),
  );
  validate("Agent", approved);
  expect(approved.settings).toMatchObject({
    autonomy_enabled: false,
    discord_publish: false,
  });
  const second = await unwrap(
    await client.POST("/api/v1/agents/{id}/profile/revisions", {
      params,
      body: { ...body, personality: "慎重", expected_version: first.version },
    }),
  );
  validate("ProfileRevision", second);
  expect(second.version).toBe(2);
  expect(first.personality).toBe("穏やか");
  expect((await client.POST("/api/v1/agents/{id}/profile/revisions", {
    params, body: { ...body, personality: "競合", expected_version: first.version },
  })).response.status).toBe(409);
  const latest = await unwrap(await client.GET("/api/v1/agents/{id}/profile", { params }));
  expect(latest).toEqual(second);
  const historical = await unwrap(await client.GET("/api/v1/agents/{id}/profile", {
    params: { ...params, query: { version: first.version } },
  }));
  expect(historical.personality).toBe(first.personality);
  expect((await client.GET("/api/v1/agents/{id}/profile", {
    params: { ...params, query: { version: 99 } },
  })).response.status).toBe(404);
  expect((await unwrap(await client.GET("/api/v1/users/me"))).onboarded_at).not.toBeNull();
  expect(
    (
      await client.POST("/api/v1/agents/{id}/profile/approve", {
        params,
        body: { version: 1 },
      })
    ).response.status,
  ).toBe(409);
  const privateData = await unwrap(
    await client.GET("/api/v1/agents/{id}/profile/private", { params }),
  );
  validate("PrivateInfo", privateData);
  expect(privateData.private_info).toBe("PRIVATE_SENTINEL");
  expect(
    (
      await client.GET("/api/v1/agents/{id}/profile/private", {
        params: { path: { id: "00000000-0000-4000-8000-000000000099" } },
      })
    ).response.status,
  ).toBe(404);
});
