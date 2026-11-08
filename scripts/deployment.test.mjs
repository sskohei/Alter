import { test } from "node:test";
import assert from "node:assert/strict";
import { checkWebEnvironment } from "./check_web_environment.mjs";
import { smokeWeb } from "./smoke_web.mjs";
const valid = {
  VERCEL_ENV: "preview",
  NEXT_PUBLIC_SITE_URL: "https://preview.example.invalid",
  NEXT_PUBLIC_SUPABASE_URL: "https://preview.supabase.co",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_synthetic",
  API_URL: "https://api.example.invalid",
  ALTER_PRODUCTION_SUPABASE_URL: "https://production.supabase.co",
  ALTER_REGISTERED_WEB_ORIGINS: "https://preview.example.invalid",
};
test("local build does not require deployed configuration", () =>
  assert.deepEqual(checkWebEnvironment({}), []));
test("valid isolated preview", () =>
  assert.deepEqual(checkWebEnvironment(valid), []));
for (const [name, value] of Object.entries({
  NEXT_PUBLIC_SITE_URL: "http://localhost:3000",
  NEXT_PUBLIC_SUPABASE_URL: "https://preview.supabase.co/auth",
  API_URL: "https://user:secret@api.example.invalid",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_secret_synthetic",
  ALTER_PRODUCTION_SUPABASE_URL: "https://preview.supabase.co",
  ALTER_REGISTERED_WEB_ORIGINS: "*",
  NEXT_PUBLIC_API_URL: "https://other.invalid",
  NEXT_PUBLIC_LLM_API_KEY: "synthetic",
  DISCORD_BOT_TOKEN: "synthetic",
}))
  test(`reject unsafe ${name} without values in error`, () => {
    const errors = checkWebEnvironment({ ...valid, [name]: value });
    assert.ok(errors.length);
    assert.ok(!errors.join().includes(value));
  });
test("reject service role legacy JWT", () => {
  const key = `synthetic.${Buffer.from(JSON.stringify({ role: "service_role" })).toString("base64url")}.synthetic`;
  assert.ok(
    checkWebEnvironment({ ...valid, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: key })
      .length,
  );
});
test("production approval binds current commit", () => {
  const production = {
    ...valid,
    VERCEL_ENV: "production",
    ALTER_PREVIEW_SUPABASE_URL: "https://other.supabase.co",
    VERCEL_GIT_COMMIT_SHA: "a".repeat(40),
    ALTER_APPROVED_PRODUCTION_COMMIT: "a".repeat(40),
  };
  assert.deepEqual(checkWebEnvironment(production), []);
  assert.ok(
    checkWebEnvironment({
      ...production,
      ALTER_APPROVED_PRODUCTION_COMMIT: "b".repeat(40),
    }).length,
  );
});
const paths = Object.fromEntries(
  [
    "/api/v1/users/me",
    "/api/v1/agents",
    "/api/v1/conversations",
    "/api/v1/discord/link/start",
    "/api/v1/users/me/deletion",
  ].map((path) => [path, {}]),
);
function fake(change = () => {}) {
  return async (value, init) => {
    const url = new URL(value);
    let status = 200;
    let body = "";
    const headers = {
      "Cache-Control": "private, no-store",
      "Referrer-Policy": "same-origin",
      "X-Content-Type-Options": "nosniff",
    };
    if (url.pathname === "/dashboard") {
      status = 307;
      headers.Location = "/login?next=%2Fdashboard";
    } else if (url.pathname.startsWith("/auth/")) {
      status = 307;
      headers.Location = "/login?error=oauth";
    } else if (url.pathname === "/openapi.json")
      body = JSON.stringify({ paths });
    else if (init.method === "OPTIONS") {
      headers.Vary = "Origin";
      if (init.headers.Origin !== "https://unregistered.invalid")
        headers["Access-Control-Allow-Origin"] = init.headers.Origin;
    } else if (url.pathname.startsWith("/api/")) status = 401;
    const response = { status, headers, body };
    change(url, init, response);
    return new Response(response.body, {
      status: response.status,
      headers: response.headers,
    });
  };
}
const smoke = (fetcher) =>
  smokeWeb({
    web: "https://preview.example.invalid",
    api: "https://api.example.invalid",
    fetcher,
  });
test("smoke verifies unauthenticated routes, cache, CORS, live paths", async () =>
  assert.deepEqual(await smoke(fake()), []));
test("smoke refuses wildcard CORS", async () =>
  assert.ok(
    (
      await smoke(
        fake((url, init, response) => {
          if (init.method === "OPTIONS")
            response.headers["Access-Control-Allow-Origin"] = "*";
        }),
      )
    ).includes("unregistered CORS origin accepted"),
  ));
test("smoke refuses scaffold-only OpenAPI", async () =>
  assert.ok(
    (
      await smoke(
        fake((url, init, response) => {
          if (url.pathname === "/openapi.json")
            response.body = '{"paths":{"/health":{}}}';
        }),
      )
    ).some((error) => error.startsWith("live contract missing")),
  ));
test("smoke detects cache and off-site callback regressions", async () => {
  const errors = await smoke(
    fake((url, init, response) => {
      if (url.pathname.startsWith("/auth/")) {
        response.headers.Location =
          "https://unregistered.invalid/?code=synthetic";
        delete response.headers["Cache-Control"];
      }
    }),
  );
  assert.ok(errors.includes("safe OAuth failure redirect"));
  assert.ok(errors.some((error) => error.endsWith("no-store")));
});
test("smoke reports network failure without sensitive URL", async () => {
  assert.deepEqual(
    await smoke(async () => {
      throw new Error("secret");
    }),
    ["request or response failed (inspect private service logs)"],
  );
});
