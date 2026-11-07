import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./support/sign-in";
const fields: Record<string, string> = {
  AI分身の名前: "こはる",
  性格: "穏やかで好奇心旺盛",
  大切にしている価値観: "誠実さ",
  "興味・関心": "読書、散歩",
  話し方: "丁寧に話す",
  交流の方針: "相手を尊重する",
  公開してよい情報: "好きな本",
  話してほしくない内容: "個人を特定する話題",
};
async function fill(page: Page) {
  for (const [label, value] of Object.entries(fields))
    await page.getByLabel(label, { exact: true }).fill(value);
}
async function save(page: Page) {
  await page.getByRole("button", { name: "下書きを保存して確認" }).click();
  await expect(
    page.getByRole("heading", { name: /版 \d+ の確認・本人承認/ }),
  ).toBeVisible();
}
async function approve(page: Page) {
  await page
    .getByRole("checkbox", { name: /この人格の公開可能な項目/ })
    .focus();
  await page.keyboard.press("Space");
  await page.getByRole("button", { name: "この版を承認する" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("alertdialog").getByRole("button", { name: "戻る" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("alertdialog").getByRole("button", { name: "承認して保存" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/agent$/);
}
test("manual draft → preview → approve → edit → revision approval; private data stays in edit", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/agent");
  await expect(
    page.getByRole("heading", { name: "まだAI分身がいません" }),
  ).toBeVisible();
  const privateRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().endsWith("/profile/private")) privateRequests.push(req.url());
  });
  await page.getByRole("link", { name: "手動で入力する", exact: true }).click();
  await fill(page);
  await page
    .getByLabel("非公開の補足（任意）", { exact: true })
    .fill("PRIVATE_SENTINEL");
  expect(
    await page.evaluate(() => JSON.stringify(sessionStorage)),
  ).not.toContain("PRIVATE_SENTINEL");
  await save(page);
  await expect(
    page.getByRole("button", { name: "この版を承認する" }),
  ).toBeDisabled();
  expect(privateRequests).toHaveLength(0);
  await expect(
    page.getByRole("region", { name: "人格の確認" }),
  ).not.toContainText("PRIVATE_SENTINEL");
  await page.getByRole("button", { name: "AI提案を試す" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "手動入力",
  );
  await page.screenshot({
    path: `../../docs/images/frontend-agent/${test.info().project.name}-preview.png`,
    fullPage: true,
  });
  await approve(page);
  await expect(
    page.getByRole("heading", { name: "承認済み · 版 1" }),
  ).toBeVisible();
  await expect(page.getByRole("main")).toContainText(
    "自律交流: OFF / Discord公開: OFF",
  );
  await expect(page.getByRole("main")).not.toContainText("PRIVATE_SENTINEL");
  await page.getByRole("link", { name: "人格を編集・承認する" }).click();
  await expect(
    page.getByLabel("非公開の補足（任意）", { exact: true }),
  ).toHaveValue("PRIVATE_SENTINEL");
  expect(privateRequests).toHaveLength(1);
  await page.getByLabel("性格", { exact: true }).fill("落ち着いて考える");
  await save(page);
  await expect(page.getByRole("region", { name: "人格の確認" })).toContainText(
    "変更前",
  );
  await page.screenshot({
    path: `../../docs/images/frontend-agent/${test.info().project.name}-revision.png`,
    fullPage: true,
  });
  await approve(page);
  await expect(
    page.getByRole("heading", { name: "承認済み · 版 2" }),
  ).toBeVisible();
  await expect(page.getByRole("main")).toContainText("落ち着いて考える");
  await expect(page.getByRole("main")).toContainText(
    "自律交流: OFF / Discord公開: OFF",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("three-step onboarding restores public draft and opens manual editor", async ({
  page,
}) => {
  await signIn(page);
  await page
    .getByLabel("AI分身の名前", { exact: true })
    .fill(fields["AI分身の名前"]);
  await page.getByLabel("性格", { exact: true }).fill(fields["性格"]);
  await page
    .getByLabel("大切にしている価値観", { exact: true })
    .fill(fields["大切にしている価値観"]);
  await page.reload();
  await expect(page.getByLabel("AI分身の名前", { exact: true })).toHaveValue(
    "こはる",
  );
  await page.getByRole("button", { name: "次へ" }).click();
  await page
    .getByLabel("興味・関心", { exact: true })
    .fill(fields["興味・関心"]);
  await page.getByLabel("話し方", { exact: true }).fill(fields["話し方"]);
  await page.getByRole("button", { name: "次へ" }).click();
  for (const label of [
    "交流の方針",
    "公開してよい情報",
    "話してほしくない内容",
  ])
    await page.getByLabel(label, { exact: true }).fill(fields[label]);
  await page.getByRole("button", { name: "人格の確認へ" }).click();
  await expect(page).toHaveURL(/\/agent\/new$/);
  await expect(page.getByLabel("性格", { exact: true })).toHaveValue(
    fields["性格"],
  );
});
for (const status of [409, 422, 429, 503]) {
  test(`save ${status}: input survives and can retry`, async ({ page }) => {
    await signIn(page);
    await page.goto("/agent/new");
    await fill(page);
    await page.route("**/api/v1/agents", async (route) => {
      await route.fulfill({
        status,
        json: {
          error: {
            code: `TEST_${status}`,
            message: "synthetic",
            request_id: "00000000-0000-4000-8000-000000000001",
          },
        },
      });
      await page.unroute("**/api/v1/agents");
    });
    await page.getByRole("button", { name: "下書きを保存して確認" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
    await expect(page.getByLabel("AI分身の名前", { exact: true })).toHaveValue(
      "こはる",
    );
    await save(page);
  });
}
test("unsaved navigation can be cancelled; logout clears scoped drafts", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/agent/new");
  await page.getByLabel("AI分身の名前", { exact: true }).fill("未保存");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "ホーム" })
    .click();
  await expect(page).toHaveURL(/\/agent\/new$/);
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "ログアウト" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(
    await page.evaluate(() =>
      Object.keys(sessionStorage).some((k) => k.startsWith("alter:draft:")),
    ),
  ).toBe(false);
});

test("optional suggestion is explicit, excludes private answers, and requires a new save", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/agent/new");
  await fill(page);
  await save(page);
  await page
    .getByLabel("非公開の補足（任意）", { exact: true })
    .fill("PRIVATE_SENTINEL");
  let received: unknown;
  await page.route("**/profile/suggest", async (route) => {
    received = route.request().postDataJSON();
    await route.fulfill({
      json: {
        version: 2,
        personality: "提案の性格",
        values_text: "提案の価値観",
        interests: ["音楽"],
        speaking_style: "丁寧",
        interaction_policy: "尊重",
        shareable_info: "音楽の趣味",
        restrictions: "なし",
        approved_at: null,
      },
    });
  });
  await page.getByRole("button", { name: "AI提案を試す" }).click();
  await expect(
    page.getByRole("button", { name: "この提案を編集に使う" }),
  ).toBeVisible();
  expect(JSON.stringify(received)).not.toContain("PRIVATE_SENTINEL");
  await expect(page.getByLabel("性格", { exact: true })).toHaveValue(
    fields["性格"],
  );
  await page.getByRole("button", { name: "この提案を編集に使う" }).click();
  await expect(page.getByLabel("性格", { exact: true })).toHaveValue(
    "提案の性格",
  );
  await expect(
    page.getByLabel("非公開の補足（任意）", { exact: true }),
  ).toHaveValue("PRIVATE_SENTINEL");
  await expect(
    page.getByRole("button", { name: "この版を承認する" }),
  ).toHaveCount(0);
  await save(page);
  await approve(page);
  await expect(page.getByRole("main")).toContainText("提案の性格");
});
