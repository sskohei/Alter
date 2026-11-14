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
  await expect(page.getByRole("main")).toContainText("/agent publish state:on");
  await expect(page.getByRole("main")).toContainText("Discord DM通知: OFF");
  await expect(page.getByRole("main")).toContainText("公開先未設定サーバー");
  await expect(page.getByRole("main")).toContainText("未設定・停止中");
  await expect(page.getByRole("main")).toContainText("未同意・撤回済み");
  // Simulate a subsequent Bot consent; Web only reads the resulting API state.
  await page.route("**/api/v1/discord/guilds", async (route) => {
    const upstream = await route.fetch();
    const guilds = await upstream.json();
    guilds[0].revoked_at = null;
    await route.fulfill({ response: upstream, json: guilds });
  });
  await page.getByRole("button", { name: "状態を更新する" }).click();
  await expect(page.getByRole("main")).toContainText("許可済み");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({
    path: `../../docs/images/frontend-discord/${test.info().project.name}-discord.png`,
    fullPage: true,
  });
  await page.unroute("**/api/v1/discord/guilds");
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

test("disconnect failure preserves the connection until a successful retry", async ({ page }) => {
  await signIn(page, "activity");
  await provider(page);
  await page.goto("/settings/discord");
  await page.getByRole("button", { name: "Discord本人連携をはじめる" }).click();
  await expect(page.getByRole("link", { name: "テスト連携を完了" })).toBeVisible();
  // The browser receives a hash-only HttpOnly cookie, never Discord credentials.
  const stateCookie = (await page.context().cookies("http://127.0.0.1:3000")).find((cookie) => cookie.name === "alter-discord-state")!;
  expect(stateCookie.httpOnly).toBe(true);
  expect(stateCookie.sameSite).toBe("Lax");
  expect(stateCookie.value).not.toContain("access_token");
  await page.getByRole("link", { name: "テスト連携を完了" }).click();
  await expect(page.getByRole("heading", { name: "Discord連携済み", exact: true })).toBeVisible();
  await page.route("**/api/v1/discord/connection", async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    await route.fulfill({ status: 503, json: { error: { code: "SERVICE_UNAVAILABLE", message: "synthetic" } } });
  });
  await page.getByRole("button", { name: "Discord連携を解除する", exact: true }).click();
  await page.getByRole("button", { name: "連携解除を確定する" }).click();
  await expect(page.getByRole("alertdialog").getByRole("alert")).toContainText("現在この機能を利用できません");
  await expect(page.getByRole("heading", { name: "Discord連携済み", exact: true })).toBeVisible();
  await page.unroute("**/api/v1/discord/connection");
  await page.getByRole("button", { name: "連携解除を確定する" }).click();
  await expect(page.getByRole("heading", { name: "Discord未連携", exact: true })).toBeVisible();
  // Browser callers cannot use the OAuth code exchange as a generic proxy.
  for (const endpoint of ["start", "complete"]) {
    expect((await page.request.post(`/api/v1/discord/link/${endpoint}`)).status()).toBe(404);
  }
});
