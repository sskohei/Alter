import { expect, test } from "@playwright/test";

test("生成クライアントからMock APIを呼び、画面内に接続結果を表示する", async ({ page }) => {
  await page.route("**/health", (route) => route.fulfill({
    json: { status: "ok", service: "api", mode: "mock" },
    headers: { "access-control-allow-origin": "*" },
  }));
  await page.goto("/development");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("開発環境の準備ができました");
  await page.getByRole("button", { name: "APIの接続を確認" }).click();
  await expect(page.getByRole("status")).toHaveText("API: ok / mock");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
