import { expect, test } from "@playwright/test";
import { signIn } from "./support/sign-in";
const firstTitle = "記憶候補 1: 本や散歩について会話したという記録。";
test("memory source, cancelled deletion, rollback, successful deletion and paging persist", async ({
  page,
}) => {
  await signIn(page, "activity");
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.goto("/memories");
  await expect(page.getByRole("article")).toHaveCount(10);
  const first = page.getByRole("article").first();
  await expect(first).toContainText("重要度 5 / 5");
  await page.screenshot({
    path: `../../docs/images/frontend-memory/${test.info().project.name}-memories.png`,
    fullPage: true,
  });
  await first.getByRole("button", { name: "この記憶を削除する" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "キャンセル" })
    .click();
  await expect(
    page.getByRole("heading", { name: firstTitle, exact: true }),
  ).toBeVisible();
  await page.route("**/api/v1/memories/*", async (route) => {
    await route.fulfill({
      status: 503,
      json: { error: { code: "UNAVAILABLE", message: "synthetic" } },
    });
    await page.unroute("**/api/v1/memories/*");
  });
  await first.getByRole("button", { name: "この記憶を削除する" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "記憶の削除を確定する" })
    .click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "記憶を削除できませんでした",
  );
  await expect(
    page.getByRole("heading", { name: firstTitle, exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(10);
  await page
    .getByRole("article")
    .first()
    .getByRole("button", { name: "この記憶を削除する" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "記憶の削除を確定する" })
    .click();
  await expect(
    page.getByRole("heading", { name: firstTitle, exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("記憶を削除しました。");
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.getByRole("article")).toHaveCount(2);
  await page.getByRole("button", { name: "前のページ" }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: firstTitle, exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("article")).toHaveCount(10);
  await expect(page.getByRole("article").first()).toContainText(
    "出典の会話は削除済み",
  );
  await page
    .getByRole("article")
    .filter({ hasText: "記憶候補 3:" })
    .getByRole("link", { name: "出典の会話を見る" })
    .click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "対象が見つかりません",
  );
  await page.getByRole("link", { name: "記憶一覧へ", exact: true }).click();
  await page
    .getByRole("article")
    .filter({ hasText: "記憶候補 4:" })
    .getByRole("link", { name: "出典の会話を見る" })
    .click();
  await expect(
    page.getByRole("list", { name: "AI分身の発言" }).getByRole("listitem"),
  ).toHaveCount(2);
  expect(requests.some((url) => url.endsWith("/profile/private"))).toBe(false);
});
test("relationship labels, paged lists and persisted block/unblock", async ({
  page,
}) => {
  await signIn(page, "activity");
  await page.goto("/relationships");
  await expect(page.getByRole("article")).toHaveCount(10);
  await expect(page.getByRole("article").nth(0)).toContainText("初対面");
  await expect(page.getByRole("article").nth(1)).toContainText("顔見知り");
  await expect(page.getByRole("article").nth(2)).toContainText(
    "よく交流する相手",
  );
  await expect(page.getByRole("main")).toContainText(
    "本人同士の友人関係や相性を示すものではありません",
  );
  await page.screenshot({
    path: `../../docs/images/frontend-memory/${test.info().project.name}-relationships.png`,
    fullPage: true,
  });
  await page
    .getByRole("article")
    .first()
    .getByRole("button", { name: "この相手をブロックする" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "ブロックを確定する" })
    .click();
  await expect(
    page
      .getByRole("article")
      .first()
      .getByRole("button", { name: "ブロックを解除する", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole("article").first()).toContainText(
    "この相手はブロック中です",
  );
  await page
    .getByRole("article")
    .first()
    .getByRole("button", { name: "ブロックを解除する", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "解除を確定する" })
    .click();
  await page.reload();
  await expect(
    page
      .getByRole("article")
      .first()
      .getByRole("button", { name: "この相手をブロックする" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.getByRole("article")).toHaveCount(2);
  await page.getByRole("button", { name: "前のページ" }).click();
  await expect(page.getByRole("article")).toHaveCount(10);
  await page.setViewportSize({ width: 320, height: 812 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("empty resources and denied memory data show safe recovery", async ({
  page,
}) => {
  await signIn(page, "activity-empty");
  await page.goto("/memories");
  await expect(
    page.getByRole("heading", { name: "記憶はまだありません" }),
  ).toBeVisible();
  await page.goto("/relationships");
  await expect(
    page.getByRole("heading", { name: "交流相手はまだいません" }),
  ).toBeVisible();
  await page.route("**/api/v1/memories?**", (route) =>
    route.fulfill({
      status: 403,
      json: { error: { code: "FORBIDDEN", message: "synthetic" } },
    }),
  );
  await page.goto("/memories");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "権限がありません",
  );
  await expect(page.getByRole("article")).toHaveCount(0);
  await page.unroute("**/api/v1/memories?**");
  await page.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "記憶はまだありません" }),
  ).toBeVisible();
});
test("loading state, unknown owner response and account switching reveal no old memories", async ({
  page,
}) => {
  await signIn(page, "activity");
  let release!: () => void;
  const pause = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/memories?**", async (route) => {
    await pause;
    await route.continue();
  });
  await page.goto("/memories");
  await expect(page.getByRole("status")).toHaveText("読み込み中…");
  release();
  await expect(page.getByRole("article")).toHaveCount(10);
  await page.unroute("**/api/v1/memories?**");
  await page.route("**/api/v1/memories?**", (route) =>
    route.fulfill({
      status: 404,
      json: { error: { code: "NOT_FOUND", message: "synthetic" } },
    }),
  );
  await page.reload();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "対象が見つかりません",
  );
  await expect(
    page.getByRole("heading", { name: firstTitle, exact: true }),
  ).toHaveCount(0);
  await page.unroute("**/api/v1/memories?**");
  await page.getByRole("button", { name: "ログアウト" }).click();
  await signIn(page, "second");
  await page.goto("/memories");
  await expect(
    page.getByRole("heading", { name: "AI分身を作成してください" }),
  ).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
});
