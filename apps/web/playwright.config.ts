import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:3000" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 375, height: 812 },
      },
    },
  ],
  webServer: [
    {
      command:
        "pnpm --filter @alter/api-client exec tsx ../../apps/web/e2e/support/mock-service.ts",
      url: "http://127.0.0.1:8001/health",
      reuseExistingServer: false,
    },
    {
      command: "pnpm start --hostname 127.0.0.1",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ALTER_LLM_NAME: "合成データ専用LLM（外部送信なし）",
        ALTER_LLM_DATA_POLICY_URL: "http://127.0.0.1:3000/legal",
        NEXT_PUBLIC_SITE_URL: "http://127.0.0.1:3000",
        API_URL: "http://127.0.0.1:8001",
        NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:8001",
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "synthetic-public-key",
      },
    },
  ],
});
