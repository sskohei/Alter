import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFile } from "node:fs/promises";
import { signIn } from "./support/sign-in";

for (let number = 1; number <= 10; number++) {
  test(`@mock-ui synthetic account ${number}: creation, approval, budgets, explicit ON and keyboard stop`, async ({
    page,
  }) => {
    const alias = `mvp${String(number).padStart(2, "0")}`;
    await signIn(page, alias, number % 2 ? "Google" : "GitHub");
    const me = await (await page.request.get("/api/v1/users/me")).json();
    expect(me.id).toBe(
      `80000000-0000-4000-8000-${String(number).padStart(12, "0")}`,
    );
    await page.goto("/agent/new");
    for (const label of [
      "AI分身の名前",
      "性格",
      "大切にしている価値観",
      "興味・関心",
      "話し方",
      "交流の方針",
      "公開してよい情報",
      "話してほしくない内容",
    ])
      await page
        .getByLabel(label, { exact: true })
        .fill(`${alias}の合成データ`);
    await page.getByRole("button", { name: "下書きを保存して確認" }).click();
    await page
      .getByRole("checkbox", { name: /この人格の公開可能な項目/ })
      .check();
    await page.getByRole("button", { name: "この版を承認する" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "承認して保存" })
      .click();
    await expect(page).toHaveURL(/\/agent$/);
    await expect(page.getByRole("main")).toContainText(
      "自律交流: OFF / Discord公開: OFF",
    );
    // Mock per-user state and BFF path forwarding only; live ownership is a separate gate.
    const foreign = await page.request.get(
      "/api/v1/agents/90000000-0000-4000-8000-000000000099/profile",
    );
    expect(foreign.status()).toBe(404);
    await page.goto("/settings/interaction");
    await page.getByLabel("1日の交流回数", { exact: true }).selectOption("1");
    await page.getByLabel("日次予算（USD）", { exact: true }).fill("0.2");
    await page
      .getByLabel("1会話の費用上限（USD）", { exact: true })
      .fill("0.02");
    await page.getByRole("button", { name: "頻度・予算を保存" }).click();
    await expect(page.getByRole("status")).toHaveText("設定を保存しました。");
    await page
      .getByRole("button", { name: "交流をONにする", exact: true })
      .click();
    await page.getByRole("alertdialog").getByRole("checkbox").check();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "交流の開始を確定する" })
      .click();
    await expect(
      page.getByRole("heading", { name: "自律交流: ON", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "交流を停止する", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "停止を確定する" })
      .focus();
    await page.keyboard.press("Enter");
    await page.reload();
    await expect(
      page.getByRole("heading", {
        name: "自律交流: OFF",
        exact: true,
        includeHidden: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Discord全体公開: OFF", exact: true }),
    ).toBeVisible();
  });
}

for (const width of [375, 320])
  test(`@mock-ui WCAG scans, keyboard dialog and overflow at ${width}px`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(120000);
    await page.setViewportSize({ width, height: 812 });
    await signIn(page, "activity");
    for (const path of [
      "/dashboard",
      "/agent",
      "/agent/edit",
      "/conversations",
      "/conversations/30000000-0000-4000-8000-000000000001",
      "/notifications",
      "/memories",
      "/relationships",
      "/settings/interaction",
      "/settings/account",
      "/settings/discord",
    ]) {
      await page.goto(path);
      await expect(
        page.getByRole("main").getByRole("heading", { level: 1 }),
      ).toBeVisible();
      await expect(page.getByText("読み込み中…", { exact: true })).toHaveCount(
        0,
      );
      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const artifactName = `axe-${width}-${path.replaceAll("/", "_")}.json`;
      const artifact = testInfo.outputPath(artifactName);
      await writeFile(
        artifact,
        JSON.stringify({
          path,
          width,
          violations: result.violations,
          incomplete: result.incomplete,
        }),
      );
      await testInfo.attach(artifactName, {
        path: artifact,
        contentType: "application/json",
      });
      expect(result.violations, path).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        path,
      ).toBe(true);
    }
    await page.goto("/memories");
    await page
      .getByRole("article")
      .first()
      .getByRole("button", { name: "この記憶を削除する" })
      .focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("alertdialog").getByRole("button", { name: "キャンセル" }),
    ).toBeFocused();
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(
      page
        .getByRole("article")
        .first()
        .getByRole("button", { name: "この記憶を削除する" }),
    ).toBeFocused();
  });
