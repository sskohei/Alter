import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./support/sign-in";
async function provider(page: Page, mode = "success") {
  let callback = "";
  await page.route("https://discord.com/oauth2/authorize**", async (route) => {
    const oauth = new URL(route.request().url());
    const target = new URL(oauth.searchParams.get("redirect_uri")!);
    target.searchParams.set("state", oauth.searchParams.get("state")!);
    if (mode === "cancel") target.searchParams.set("error", "access_denied");
    else
      target.searchParams.set(
        "code",
        mode === "success" ? "synthetic-code" : mode,
      );
    callback = target.toString();
    await route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: `<html lang="ja"><body><a href="${callback.replaceAll("&", "&amp;")}">テスト連携を完了</a></body></html>`,
    });
  });
  return () => callback;
}
test("link is independent of publishing, replay is rejected, disconnect revokes state", async ({
  page,
}) => {
  await signIn(page, "activity");
  const callback = await provider(page);
  await page.goto("/settings/discord");
  await expect(
    page.getByRole("heading", { name: "Discord未連携", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Discord本人連携をはじめる" }).click();
  await page.getByRole("link", { name: "テスト連携を完了" }).click();
  await expect(
    page.getByRole("heading", { name: "Discord連携済み", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("main")).toContainText("Discord全体公開: OFF");
  await expect(page.getByRole("main")).toContainText("/agent publish on");
  await page.screenshot({
    path: `../../docs/images/frontend-discord/${test.info().project.name}-discord.png`,
    fullPage: true,
  });
  await page.goto(callback());
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "失効したか、一致しません",
  );
  await expect(
    page.getByRole("heading", { name: "Discord連携済み", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Discord連携を解除する", exact: true })
    .click();
  await expect(page.getByRole("alertdialog")).toContainText(
    "完全に回収できません",
  );
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "キャンセル" })
    .click();
  await page
    .getByRole("button", { name: "Discord連携を解除する", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "連携解除を確定する" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Discord未連携", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("main")).toContainText("未同意・撤回済み");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Discord未連携", exact: true }),
  ).toBeVisible();
});
for (const [mode, message] of [
  ["cancel", "キャンセルしました"],
  ["expired", "失効したか、一致しません"],
  ["conflict", "別の連携と競合"],
])
  test(`Discord callback ${mode}`, async ({ page }) => {
    await signIn(page);
    await provider(page, mode);
    await page.goto("/settings/discord");
    await page
      .getByRole("button", { name: "Discord本人連携をはじめる" })
      .click();
    await page.getByRole("link", { name: "テスト連携を完了" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toContainText(
      message,
    );
    await expect(
      page.getByRole("heading", { name: "Discord未連携", exact: true }),
    ).toBeVisible();
  });
test("unconfigured guild and permission error have safe recovery", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/settings/discord");
  await expect(page.getByRole("main")).toContainText(
    "確認できるサーバーがありません",
  );
  await page.route("**/api/v1/discord/guilds", (route) =>
    route.fulfill({
      status: 403,
      json: { error: { code: "FORBIDDEN", message: "synthetic" } },
    }),
  );
  await page.reload();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "権限がありません",
  );
  await page.unroute("**/api/v1/discord/guilds");
  await page.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Discord未連携", exact: true }),
  ).toBeVisible();
});
