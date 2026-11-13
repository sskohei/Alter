import { expect, test } from "@playwright/test";

test("生成クライアントからMock APIを呼び、画面内に接続結果を表示する", async ({ page }) => {
  await page.route("**/health", (route) => route.fulfill({
    json: { status: "ok", service: "api", mode: "mock" },
    headers: { "access-control-allow-origin": "*" },
  }));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("開発環境の準備ができました");
  await page.getByRole("button", { name: "APIの接続を確認" }).click();
  await expect(page.getByRole("status")).toHaveText("API: ok / mock");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("Webから通常・空一覧・全エラーと入力不正を再現する", async ({ page }) => {
  await page.goto("/development/mock");
  await page.getByRole("button", { name: "プロフィール取得" }).click();
  await expect(page.getByRole("status")).toHaveText("プロフィール: 招待ユーザー");
  await page.getByRole("button", { name: "合成一覧を取得" }).click();
  await expect(page.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.getByRole("listitem")).toHaveText(["合成ユーザー3"]);
  await page.getByLabel("応答シナリオ").selectOption("empty");
  await page.getByRole("button", { name: "合成一覧を取得" }).click();
  await expect(page.getByRole("status")).toHaveText("一覧は空です");
  for (const scenario of ["401", "403", "inactive", "404", "409", "422", "429", "503"]) {
    await page.getByLabel("応答シナリオ").selectOption(scenario);
    await page.getByRole("button", { name: "プロフィール取得" }).click();
    await expect(page.getByRole("status")).toContainText(`HTTP ${scenario === "inactive" ? "403" : scenario}`);
    await expect(page.getByRole("status")).toContainText("request_id:");
    await expect(page.getByRole("listitem")).toHaveCount(0);
  }
  await page.getByLabel("応答シナリオ").selectOption("normal");
  await page.getByLabel("表示名").fill(" ");
  await page.getByRole("button", { name: "表示名を保存" }).click();
  await expect(page.getByRole("status")).toContainText("HTTP 422");
  await page.getByLabel("表示名").fill(" 更新名 ");
  await page.getByRole("button", { name: "表示名を保存" }).click();
  await expect(page.getByRole("status")).toHaveText("プロフィール: 更新名");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/mock-contract-${test.info().project.name}.png`, fullPage: true });
});
