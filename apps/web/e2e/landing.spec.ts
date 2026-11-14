import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("トップ: アクセシビリティ違反がなく、横スクロールせず、ログインへ進める", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("もうひとりの自分が");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("link", { name: "招待を受けた方はこちら" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("トップ: キーボードだけで本文へ移動し、動きを止められる", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "本文へ移動" })).toBeFocused();
  const toggle = page.getByRole("button", { name: "動きを止める" });
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).toHaveAttribute("data-motion", "off");
  await page.reload();
  await expect(page.getByRole("button", { name: "動きを止める" })).toHaveAttribute("aria-pressed", "true");
});

test.describe("動きを減らす設定", () => {
  test.use({ reducedMotion: "reduce" });
  test("OSの設定に合わせて、最初から止まっている", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "動きを止める" })).toHaveAttribute("aria-pressed", "true");
    const state = await page.locator(".lp-ticker .track").first().evaluate((el) => getComputedStyle(el).animationPlayState);
    expect(state).toBe("paused");
  });
});
