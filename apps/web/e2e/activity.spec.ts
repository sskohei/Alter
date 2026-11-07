import { expect, test } from "@playwright/test";
import { signIn } from "./support/sign-in";
const id = "30000000-0000-4000-8000-000000000001";
const runningId = "30000000-0000-4000-8000-000000000002";
test("dashboard, paged conversations, notifications and transcript", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "こはる", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("main")).toContainText("未読が3件以上あります");
  await page.screenshot({
    path: `../../docs/images/frontend-activity/${test.info().project.name}-dashboard.png`,
    fullPage: true,
  });
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "交流", exact: true })
    .click();
  await expect(page.getByRole("article")).toHaveCount(10);
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.getByRole("article")).toHaveCount(2);
  await page.getByRole("button", { name: "前のページ" }).click();
  await expect(page.getByRole("article")).toHaveCount(10);
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "通知", exact: true })
    .click();
  const notice = page.getByRole("article").first();
  await page.route("**/notifications/*/read", async (route) => {
    await route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "synthetic" } },
    });
    await page.unroute("**/notifications/*/read");
  });
  await notice.getByRole("button", { name: "既読にする" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "既読にできませんでした",
  );
  await expect(
    notice.getByRole("button", { name: "既読にする" }),
  ).toBeEnabled();
  await notice.getByRole("button", { name: "既読にする" }).click();
  await expect(notice.getByRole("button")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("article").first()).toContainText("既読");
  await page
    .getByRole("article")
    .first()
    .getByRole("link", { name: "会話を見る" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/conversations/${id}$`));
  await expect(
    page.getByRole("list", { name: "AI分身の発言" }).getByRole("listitem"),
  ).toHaveCount(2);
  const turns = await page
    .getByRole("list", { name: "AI分身の発言" })
    .getByRole("listitem")
    .allTextContents();
  expect(turns[0]).toContain("発言 1");
  expect(turns[1]).toContain("発言 2");
  await expect(page.getByRole("main")).toContainText("Web参加者限定");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `../../docs/images/frontend-activity/${test.info().project.name}-conversation.png`,
    fullPage: true,
  });
});
test("empty and unauthorized conversations never display another participant's data", async ({
  page,
}) => {
  await signIn(page, "activity-empty");
  await page.goto("/conversations");
  await expect(
    page.getByRole("heading", { name: "まだ交流がありません" }),
  ).toBeVisible();
  await page.goto(`/conversations/${id}`);
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "対象が見つかりません",
  );
  await expect(
    page.getByText("最近読んだ本の話をしませんか。", { exact: true }),
  ).toHaveCount(0);
  await page.goto("/notifications");
  await expect(
    page.getByRole("heading", { name: "通知はまだありません" }),
  ).toBeVisible();
});
test("detail polls after 15 seconds and stops after completion", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.clock.install();
  let calls = 0;
  await page.route(`**/api/v1/conversations/${runningId}`, async (route) => {
    calls++;
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      json:
        calls > 1
          ? { ...body, status: "completed", summary: "会話が完了しました。" }
          : body,
    });
  });
  await page.goto(`/conversations/${runningId}`);
  await expect(page.getByRole("main")).toContainText("進行中");
  expect(calls).toBe(1);
  await page.clock.fastForward(15_000);
  await expect(page.getByRole("main")).toContainText("会話が完了しました。");
  expect(calls).toBe(2);
  await page.clock.fastForward(30_000);
  expect(calls).toBe(2);
});
test("list polls every 30 seconds and a new account sees an empty list", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.clock.install();
  let calls = 0;
  await page.route("**/api/v1/conversations?**", async (route) => {
    calls++;
    await route.continue();
  });
  await page.goto("/conversations");
  await expect(page.getByRole("article")).toHaveCount(10);
  expect(calls).toBe(1);
  await page.clock.fastForward(30_000);
  await expect.poll(() => calls).toBe(2);
  await page.getByRole("button", { name: "ログアウト" }).click();
  await signIn(page, "second");
  await page.goto("/conversations");
  await expect(
    page.getByRole("heading", { name: "まだ交流がありません" }),
  ).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
});
