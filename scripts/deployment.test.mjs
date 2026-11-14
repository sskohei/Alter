import { test } from "node:test";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { checkWebEnvironment } from "./check_web_environment.mjs";
import { smokeWeb, requiredOperations } from "./smoke_web.mjs";
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
  SUPABASE_STORAGE_SECRET_KEY: "synthetic",
  ANTHROPIC_API_KEY: "synthetic",
  AGENT_IDEMPOTENCY_HMAC_KEY_V1: "synthetic",
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
  Object.entries(requiredOperations).map(([path, methods]) => [
    path,
    Object.fromEntries(
      methods.map((method) => [method, { responses: { "200": {} } }]),
    ),
  ]),
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
      "X-Frame-Options": "DENY",
    };
    if (url.pathname === "/dashboard") {
      status = 307;
      headers.Location = "/login?next=%2Fdashboard";
    } else if (url.pathname.startsWith("/auth/")) {
      status = 307;
      headers["Referrer-Policy"] = "no-referrer";
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

for (const value of [
  "https://preview.example.invalid/",
  "https://PREVIEW.example.invalid",
  "https://preview.example.invalid:443",
]) {
  test(`reject noncanonical registered origin ${value}`, () => {
    assert.ok(checkWebEnvironment({ ...valid, NEXT_PUBLIC_SITE_URL: value }).length);
  });
}
test("smoke rejects public API profiles and cached protected redirects", async () => {
  const errors = await smoke(
    fake((url, init, response) => {
      if (
        url.hostname === "api.example.invalid" &&
        init.method !== "OPTIONS" &&
        url.pathname === "/api/v1/users/me"
      ) response.status = 200;
      if (url.pathname === "/dashboard") delete response.headers["Cache-Control"];
    }),
  );
  assert.ok(errors.includes("anonymous API rejection"));
  assert.ok(errors.includes("protected page: no-store"));
});

test("smoke rejects path-only schema without implemented operations", async () => {
  const errors = await smoke(
    fake((url, init, response) => {
      if (url.pathname === "/openapi.json") {
        response.body = JSON.stringify({
          paths: Object.fromEntries(Object.keys(paths).map((path) => [path, {}])),
        });
      }
    }),
  );
  assert.ok(errors.includes("live contract missing: POST /api/v1/agents"));
});

test("smoke operations exist in the formal generated OpenAPI", () => {
  const schema = JSON.parse(readFileSync(
    new URL("../packages/api-client/openapi.json", import.meta.url), "utf8",
  ));
  for (const [path, methods] of Object.entries(requiredOperations)) {
    for (const method of methods) {
      assert.ok(schema.paths[path]?.[method]?.responses, `${method} ${path}`);
    }
  }
});
