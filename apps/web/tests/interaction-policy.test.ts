import { afterEach, expect, test, vi } from "vitest";
import { interactionDisclosure } from "../lib/interaction-policy";
afterEach(() => vi.unstubAllEnvs());
test("missing provider or data conditions cannot authorize starting exchange", () => {
  vi.stubEnv("ALTER_LLM_NAME", "");
  vi.stubEnv("ALTER_LLM_DATA_POLICY_URL", "");
  expect(interactionDisclosure()).toBeNull();
  vi.stubEnv("ALTER_LLM_NAME", "Reviewed provider");
  expect(interactionDisclosure()).toBeNull();
});
for (const value of [
  "javascript:alert(1)",
  "https://name:password@example.test/policy",
  "http://example.test/policy",
  "bad",
]) {
  test(`rejects unsafe disclosure URL ${value}`, () => {
    vi.stubEnv("ALTER_LLM_NAME", "Provider");
    vi.stubEnv("ALTER_LLM_DATA_POLICY_URL", value);
    expect(interactionDisclosure()).toBeNull();
  });
}
test("reviewed public disclosure is explicitly supplied by server configuration", () => {
  vi.stubEnv("ALTER_LLM_NAME", "Provider");
  vi.stubEnv("ALTER_LLM_DATA_POLICY_URL", "https://example.test/policy");
  expect(interactionDisclosure()).toEqual({
    name: "Provider",
    policyUrl: "https://example.test/policy",
  });
});
