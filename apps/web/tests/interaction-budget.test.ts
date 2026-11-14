import { expect, test } from "vitest";
import { interactionBudgetSchema } from "../lib/interaction-budget";
const valid = { frequency_per_day: 1, daily_budget_usd: 0.2, max_session_usd: 0.02 };
test("lowering the daily budget below the session cap is rejected", () => {
  const result = interactionBudgetSchema.safeParse({ ...valid, daily_budget_usd: 0.01 });
  expect(result.success).toBe(false);
  if (!result.success) expect(result.error.issues[0].path).toEqual(["max_session_usd"]);
});
test("USD inputs allow six decimal places including small values and reject excess precision", () => {
  for (const value of [0, 0.1, 0.000001, 0.123456])
    expect(interactionBudgetSchema.safeParse({ ...valid, daily_budget_usd: value, max_session_usd: value }).success).toBe(true);
  for (const value of [0.0000001, 0.1234567, -1, NaN, Infinity])
    for (const field of ["daily_budget_usd", "max_session_usd"])
      expect(interactionBudgetSchema.safeParse({ ...valid, [field]: value }).success).toBe(false);
});
