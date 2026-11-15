import { expect, test } from "@playwright/test";
import { signIn } from "./support/sign-in";

test("manual interaction starts from the exchange page and opens its conversation", async ({ page }) => {
  await signIn(page, "activity");
  let starts = 0;
  await page.route("**/api/v1/conversations/start", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { enabled: true, unlimited_daily_count: true } });
    } else {
      starts++;
      expect(route.request().headers()["idempotency-key"]).toBeTruthy();
      await route.fulfill({ status: 202, json: { id: "30000000-0000-4000-8000-000000000001" } });
    }
  });
  await page.goto("/conversations");
  await expect(page.getByRole("heading", { name: "今すぐ交流する" })).toBeVisible();
  await expect(page.getByText("デモの手動開始には1日の回数制限がありません。", { exact: false })).toBeVisible();
  await page.screenshot({ path: `/tmp/alter89-${test.info().project.name}-manual.png`, fullPage: true });
  await page.getByRole("button", { name: "交流を開始する", exact: true }).click();
  await expect(page).toHaveURL(/\/conversations\/30000000-0000-4000-8000-000000000001$/);
  await expect(page.getByRole("heading", { name: "AI分身の会話", exact: true })).toBeVisible();
  expect(starts).toBe(1);
});
