import { expect, test } from "@playwright/test";
import { signIn } from "./support/sign-in";

test.describe("native form origin and logout", () => {
  test.use({ javaScriptEnabled: false });
  test("OAuth then logout works before hydration and clears short-lived proofs", async ({
    page,
    context,
  }) => {
    await signIn(page);
    await expect(page).toHaveURL(/\/onboarding$/);
    await context.addCookies(
      [
        "alter-delete-challenge",
        "alter-delete-proof",
        "alter-discord-state",
      ].map((name) => ({
        name,
        value: "synthetic",
        url: "http://127.0.0.1:3000",
        httpOnly: true,
        sameSite: "Lax" as const,
      })),
    );
    const response = await page.request.get("/onboarding");
    expect(response.headers()["referrer-policy"]).toBe("same-origin");
    expect(response.headers()["cache-control"]).toContain("no-store");
    await page.getByRole("button", { name: "ログアウト" }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(
      (await context.cookies()).some((cookie) =>
        cookie.name.startsWith("alter-"),
      ),
    ).toBe(false);
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });
});

test("login and OAuth failure responses apply deployment headers", async ({ request }) => {
  const login = await request.get("/login");
  expect(login.headers()["referrer-policy"]).toBe("same-origin");
  expect(login.headers()["x-content-type-options"]).toBe("nosniff");
  expect(login.headers()["x-frame-options"]).toBe("DENY");
  for (const path of [
    "/auth/callback?error=access_denied",
    "/auth/reauth/callback?error=access_denied",
    "/auth/discord/callback?error=access_denied",
  ]) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status()).toBe(307);
    expect(response.headers()["cache-control"]).toContain("no-store");
    expect(response.headers()["referrer-policy"]).toBe("no-referrer");
    expect(new URL(response.headers().location).origin).toBe("http://127.0.0.1:3000");
  }
});
