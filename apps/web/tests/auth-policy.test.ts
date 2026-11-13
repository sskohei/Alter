import { expect, test } from "vitest";
import { safeReturnPath, accessMessage } from "../lib/auth-policy";
for (const input of ["//evil.test", "https://evil.test", "/\\evil.test", "/%2f%2fevil.test", "/auth/callback", "/dashboard?next=evil"]) {
  test(`rejects return URL ${input}`, () => expect(safeReturnPath(input)).toBe("/dashboard"));
}
test("allows implemented internal routes", () => expect(safeReturnPath("/agent/edit")).toBe("/agent/edit"));
test("invitation failure has actionable wording", () => expect(accessMessage("INVITATION_REQUIRED")).toContain("招待元"));
