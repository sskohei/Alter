import type { Page } from "@playwright/test";
export async function signIn(
  page: Page,
  which = "normal",
  provider = "Google",
) {
  await page.goto("/login");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: `${provider}でログイン` }).click();
  await page.getByRole("link", { name: which, exact: true }).click();
}
