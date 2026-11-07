import { expect, test } from "@playwright/test";

import { signIn } from "./support/sign-in";
for (const provider of ["Google", "GitHub"]) {
  test(`${provider}: PKCE → invitation gate → onboarding → logout`, async ({ page }) => {
    await page.goto("/"); await page.getByRole("link", { name: "招待を受けた方はこちら" }).click();
    if (provider === "Google") await page.screenshot({ path: `../../docs/images/frontend-auth/${test.info().project.name}-login.png`, fullPage: true });
    await signIn(page, "normal", provider);
    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("navigation")).toBeVisible();
    if (provider === "Google") await page.screenshot({ path: `../../docs/images/frontend-auth/${test.info().project.name}-onboarding.png`, fullPage: true });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "ログアウト" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/dashboard"); await expect(page).toHaveURL(/\/login/);
  });
}
for (const [which, text] of [["forbidden", "招待が確認できません"], ["inactive", "アカウントは停止中"], ["unavailable", "利用資格を確認できません"], ["cancel", "ログインを完了できません"], ["failure", "ログインを完了できません"]]) {
  test(`OAuth/API denial: ${which}`, async ({ page }) => {
    await signIn(page, which);
    await expect(page.getByRole("main").getByRole("alert")).toContainText(text);
    await page.goto("/dashboard"); await expect(page).toHaveURL(/\/login/);
  });
}
test("existing user → dashboard; switching accounts removes previous profile", async ({ page }) => {
  await signIn(page, "existing"); await expect(page).toHaveURL(/\/dashboard$/);
  const response = await page.request.get("/dashboard"); expect(response.headers()["cache-control"]).toContain("no-store");
  await page.getByRole("button", { name: "ログアウト" }).click();
  await signIn(page, "second");
  await expect(page.getByText("別のユーザー", { exact: true })).toBeVisible();
  await expect(page.getByText("招待ユーザー", { exact: true })).toHaveCount(0);
});
test("expired API token refreshes once and returns to onboarding", async ({ page }) => {
  await signIn(page, "expired"); await expect(page).toHaveURL(/\/onboarding$/);
});
test("callback never redirects to an external next URL", async ({ page }) => {
  await page.goto("/auth/callback?error=access_denied&next=https://evil.example");
  await expect(page).toHaveURL(/127\.0\.0\.1:3000\/login\?error=oauth/);
});

test("login consent and OAuth can be started by keyboard", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("checkbox").focus(); await page.keyboard.press("Space");
  await page.keyboard.press("Tab"); await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "テスト専用 OAuth" })).toBeVisible();
});
