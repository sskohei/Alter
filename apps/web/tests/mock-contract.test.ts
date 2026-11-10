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
    if (!response.ok) {
      const validateLive = ajv.compile({ $ref: "live#/components/schemas/ErrorResponse" });
      expect(validateLive(body), JSON.stringify(validateLive.errors)).toBe(true);
    }
  });
}
