import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./support/sign-in";
async function openSettings(page: Page) {
  await signIn(page, "activity");
  await page.goto("/settings/interaction");
  await expect(
    page.getByRole("heading", {
      name: "自律交流: OFF",
      exact: true,
      includeHidden: true,
    }),
  ).toBeVisible();
}
async function proposeStart(page: Page) {
  await page
    .getByRole("button", { name: "交流をONにする", exact: true })
    .click();
  await expect(page.getByRole("alertdialog")).toContainText("会話を90日保存");
  await page.getByRole("alertdialog").getByRole("checkbox").check();
}
test("budgets, explicit exchange and Discord consent, persistence and keyboard stop at 320px", async ({
  page,
}) => {
  await openSettings(page);
  await expect(
    page.getByRole("heading", { name: "Discord全体公開: OFF", exact: true }),
  ).toBeVisible();
  await page.getByLabel("日次予算（USD）", { exact: true }).fill("0.4");
  await page.getByLabel("1会話の費用上限（USD）", { exact: true }).fill("0.04");
  await page.getByRole("button", { name: "頻度・予算を保存" }).click();
  await expect(page.getByRole("status")).toHaveText("設定を保存しました。");
  await expect(
    page.getByRole("heading", {
      name: "自律交流: OFF",
      exact: true,
      includeHidden: true,
    }),
  ).toBeVisible();
  await proposeStart(page);
  await expect(page.getByRole("alertdialog")).toContainText(
    "合成データ専用LLM",
  );
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "交流の開始を確定する" })
    .click();
  await expect(
    page.getByRole("heading", { name: "自律交流: ON", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Discord全体公開: OFF", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Discord公開をONにする", exact: true })
    .click();
  await expect(page.getByRole("alertdialog")).toContainText(
    "テスト交流サーバー",
  );
  await expect(page.getByRole("alertdialog")).toContainText(
    "111111111111111111",
  );
  await expect(page.getByRole("alertdialog")).toContainText("ai交流");
  await expect(page.getByRole("alertdialog")).toContainText(
    "完全に回収できません",
  );
  await page.getByRole("alertdialog").getByRole("checkbox").check();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Discord公開を確定する" })
    .click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Discord全体公開: ON", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `../../docs/images/frontend-interaction/${test.info().project.name}-settings.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 320, height: 812 });
  await expect(
    page.getByRole("heading", { name: "自律交流: ON", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `../../docs/images/frontend-interaction/${test.info().project.name}-settings-320.png`,
    fullPage: true,
  });
  // Stop is independent of unsaved invalid budget inputs.
  await page.getByLabel("日次予算（USD）", { exact: true }).fill("-1");
  await page
    .getByRole("button", { name: "交流を停止する", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "停止を確定する" })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", {
      name: "自律交流: OFF",
      exact: true,
      includeHidden: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Discord全体公開: ON", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Discord公開を停止する", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "停止を確定する" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Discord全体公開: OFF", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "自律交流: OFF",
      exact: true,
      includeHidden: true,
    }),
  ).toBeVisible();
});
for (const status of [409, 429, 503]) {
  test(`start ${status} never falsely shows ON and can retry`, async ({
    page,
  }) => {
    await openSettings(page);
    await proposeStart(page);
    await page.route("**/api/v1/agents/*/settings", async (route) => {
      await route.fulfill({
        status,
        json: { error: { code: `TEST_${status}`, message: "synthetic" } },
      });
      await page.unroute("**/api/v1/agents/*/settings");
    });
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "交流の開始を確定する" })
      .click();
    await expect(
      page.getByRole("alertdialog").getByRole("alert"),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "自律交流: OFF",
        exact: true,
        includeHidden: true,
      }),
    ).toBeVisible();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "交流の開始を確定する" })
      .click();
    await expect(
      page.getByRole("heading", { name: "自律交流: ON", exact: true }),
    ).toBeVisible();
  });
}
test("invalid input, cancellation and unknown publication target stay OFF", async ({
  page,
}) => {
  await openSettings(page);
  await page.getByLabel("日次予算（USD）", { exact: true }).fill("-1");
  await page.getByRole("button", { name: "頻度・予算を保存" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "入力内容を確認",
  );
  await page.getByLabel("日次予算（USD）", { exact: true }).fill("0.2");
  await proposeStart(page);
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "キャンセル" })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "自律交流: OFF",
      exact: true,
      includeHidden: true,
    }),
  ).toBeVisible();
  await page.route("**/api/v1/discord/guilds", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Discord公開をONにする", exact: true }),
  ).toBeDisabled();
});
test("no agent has a creation CTA; conversation block cancellation and recovery", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/settings/interaction");
  await expect(
    page.getByRole("heading", { name: "AI分身を作成してください" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "ログアウト" }).click();
  await signIn(page, "activity");
  await page.goto("/conversations/30000000-0000-4000-8000-000000000001");
  await page.getByRole("button", { name: "この相手をブロックする" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "キャンセル" })
    .click();
  await page.getByRole("button", { name: "この相手をブロックする" }).click();
  await page.route("**/blocks/*", async (route) => {
    await route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "synthetic" } },
    });
    await page.unroute("**/blocks/*");
  });
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "ブロックを確定する" })
    .click();
  await expect(page.getByRole("alertdialog").getByRole("alert")).toBeVisible();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "ブロックを確定する" })
    .click();
  await expect(
    page.getByRole("button", { name: "ブロックを解除する", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "ブロックを解除する", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "解除を確定する" })
    .click();
  await expect(
    page.getByRole("button", { name: "この相手をブロックする" }),
  ).toBeVisible();
});
