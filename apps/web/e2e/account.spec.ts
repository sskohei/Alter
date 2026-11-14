import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./support/sign-in";
async function verify(page: Page) {
  await page
    .getByRole("button", { name: "同じアカウントで本人確認をやり直す" })
    .click();
  await page
    .getByRole("link", { name: "同じアカウントで本人確認を完了" })
    .click();
  // This control appears after the hydrated page has loaded preferences.
  // The SSR deletion button can already be enabled before keyboard handlers attach.
  await expect(
    page.getByRole("checkbox", { name: "Webで交流のお知らせを受け取る", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "アカウントを削除する", exact: true }),
  ).toBeEnabled();
}
test("profile and notification updates persist and failed save preserves server state", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.goto("/settings/account");
  await page.getByLabel("表示名（80文字まで）").fill("新しい表示名");
  await page.getByRole("button", { name: "表示名を保存", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("表示名を保存しました。");
  await expect(
    page.getByRole("checkbox", { name: "Webで交流のお知らせを受け取る" }),
  ).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /Discord DMで/ }),
  ).not.toBeChecked();
  await page.getByRole("checkbox", { name: /Discord DMで/ }).click();
  await expect(page.getByRole("status")).toHaveText("通知設定を保存しました。");
  await page.reload();
  await expect(page.getByLabel("表示名（80文字まで）")).toHaveValue(
    "新しい表示名",
  );
  await expect(
    page.getByRole("checkbox", { name: /Discord DMで/ }),
  ).toBeChecked();
  await page.route("**/api/v1/preferences", async (route) => {
    await route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "synthetic" } },
    });
    await page.unroute("**/api/v1/preferences");
  });
  await page.getByRole("checkbox", { name: /Discord DMで/ }).click();
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  await expect(
    page.getByRole("checkbox", { name: /Discord DMで/ }),
  ).toBeChecked();
  await page.screenshot({
    path: `../../docs/images/frontend-account/${test.info().project.name}-account.png`,
    fullPage: true,
  });
});
test("fresh same-account OAuth, cancellation, explicit deletion and isolated progress", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.goto("/settings/account");
  await expect(
    page.getByRole("button", { name: "アカウントを削除する", exact: true }),
  ).toBeDisabled();
  const rejected = await page.request.post("/api/v1/users/me/deletion", {
    headers: { Origin: "http://127.0.0.1:3000" },
    data: { confirm: true },
  });
  expect(rejected.status()).toBe(403);
  await verify(page);
  await page
    .getByRole("button", { name: "アカウントを削除する", exact: true })
    .click();
  await expect(page.getByRole("alertdialog")).toContainText("相手からも非表示");
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "キャンセル" })
    .click();
  await page
    .getByRole("button", { name: "アカウントを削除する", exact: true })
    .click();
  await expect(
    page
      .getByRole("alertdialog")
      .getByRole("button", { name: "削除を要求する" }),
  ).toBeDisabled();
  await page.getByRole("alertdialog").getByRole("checkbox").check();
  await page.route("**/api/v1/users/me/deletion", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 429,
        headers: { "Retry-After": "1" },
        json: { error: { code: "RATE_LIMITED", message: "synthetic" } },
      });
      await page.unroute("**/api/v1/users/me/deletion");
    } else await route.continue();
  });
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "削除を要求する" })
    .click();
  await expect(page.getByRole("alertdialog").getByRole("alert")).toBeVisible();
  await expect(page.getByRole("button", { name: "削除を要求する", exact: true })).toBeDisabled();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "削除を要求する" })
    .click();
  await expect(page).toHaveURL(/\/deletion-status$/);
  await expect(
    page.getByRole("heading", { name: "削除処理中です" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "状態を再確認する" }).click();
  await expect(
    page.getByRole("heading", { name: "削除処理が完了しました" }),
  ).toBeVisible();
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login\?error=ACCOUNT_INACTIVE/);
  await page.getByRole("link", { name: "削除要求済みの方は処理状況を確認" }).click();
  await page.getByRole("link", { name: "ログインして削除状況を確認" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Googleでログイン" }).click();
  await page.getByRole("link", { name: "同じアカウントで本人確認を完了" }).click();
  await expect(page).toHaveURL(/\/deletion-status$/);
  await expect(page.getByRole("heading", { name: "削除処理が完了しました" })).toBeVisible();

  await page.goto("/deletion-status");
  await expect(
    page.getByRole("heading", { name: "削除処理が完了しました" }),
  ).toBeVisible();
});
test("cancelled or different-account OAuth cannot grant deletion", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/settings/account");
  await page
    .getByRole("button", { name: "同じアカウントで本人確認をやり直す" })
    .click();
  await page.getByRole("link", { name: "本人確認をキャンセル" }).click();
  await expect(
    page.getByRole("button", { name: "アカウントを削除する", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "同じアカウントで本人確認をやり直す" })
    .click();
  await page.getByRole("link", { name: "別のアカウントを選ぶ" }).click();
  await expect(page).toHaveURL(/\/login\?error=reauth/);
});
test("failed progress and expired Auth still show a private follow-up route", async ({ page }) => {
  await signIn(page, "activity"); let expired = false;
  await page.route("**/api/v1/users/me/deletion", route => route.fulfill(expired ? { status: 401, json: { error: { code: "UNAUTHENTICATED", message: "synthetic" } } } : { json: { id: "70000000-0000-4000-8000-000000000001", status: "failed", requested_at: "2026-11-08T00:00:00Z", completed_at: null } }));
  await page.goto("/deletion-status"); await expect(page.getByRole("heading", { name: "削除処理の確認が必要です" })).toBeVisible(); expired = true; await page.getByRole("button", { name: "状態を再確認する" }).click(); await expect(page.getByRole("main").getByRole("alert")).toContainText("有効期限"); await expect(page).toHaveURL(/\/deletion-status$/); await expect(page.getByRole("main")).toContainText("私的な連絡先");
});

test("320px keyboard cancellation, conflict and expired reauthentication recover safely", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 812 });
  await signIn(page, "activity");
  await page.goto("/settings/account");
  await verify(page);
  const trigger = page.getByRole("button", { name: "アカウントを削除する", exact: true });
  let submissions = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/users/me/deletion")) submissions++;
  });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alertdialog").getByRole("button", { name: "キャンセル" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  expect(submissions).toBe(0);
  await page.route("**/api/v1/users/me/deletion", async route => {
    await route.fulfill({ status: 409, json: { error: { code: "CONFLICT", message: "synthetic" } } });
  });
  await trigger.click();
  await page.getByRole("alertdialog").getByRole("checkbox").check();
  await page.getByRole("button", { name: "削除を要求する", exact: true }).click();
  await expect(page.getByRole("alertdialog").getByRole("alert")).toContainText("競合");
  await page.unroute("**/api/v1/users/me/deletion");
  await page.route("**/api/v1/users/me/deletion", async route => {
    await route.fulfill({ status: 403, json: { error: { code: "REAUTHENTICATION_REQUIRED", message: "synthetic" } } });
  });
  await page.getByRole("button", { name: "削除を要求する", exact: true }).click();
  await expect(page.getByRole("alertdialog").getByRole("alert")).toContainText("有効期限");
  await expect(page.getByRole("button", { name: "削除を要求する", exact: true })).toBeDisabled();
  await page.getByRole("alertdialog").getByRole("button", { name: "キャンセル" }).click();
  await expect(trigger).toBeDisabled();
  await page.unroute("**/api/v1/users/me/deletion");
  await verify(page);
  await expect(trigger).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("logout clears drafts and reauthentication cookies and denies old account pages", async ({ page, context }) => {
  await signIn(page, "activity");
  await page.goto("/settings/account");
  await verify(page);
  await page.evaluate(() => sessionStorage.setItem("alter:draft:test", "private draft"));
  expect((await context.cookies()).some(cookie => cookie.name === "alter-delete-proof")).toBe(true);
  await page.getByRole("main").getByRole("button", { name: "ログアウト", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(() => sessionStorage.getItem("alter:draft:test"))).toBeNull();
  expect((await context.cookies()).some(cookie => cookie.name === "alter-delete-proof" || cookie.name === "alter-delete-challenge")).toBe(false);
  await page.goBack();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/settings/account");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "アカウント設定", exact: true })).toHaveCount(0);
});
