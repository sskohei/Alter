// Test process only. There is no authentication bypass in application code.
import { createServer } from "node:http";
import { createMockFetch, mockError, profile } from "@alter/api-client/mock";

function user(which: string) {
  return {
    id: /^mvp\d{2}:/.test(which)
      ? `80000000-0000-4000-8000-${which.slice(3, 5).padStart(12, "0")}`
      : which.startsWith("second")
        ? "00000000-0000-4000-8000-000000000002"
        : profile.id,
    aud: "authenticated",
    role: "authenticated",
    email: "synthetic@example.test",
    app_metadata: {
      provider: /^mvp(02|04|06|08|10):/.test(which) ? "github" : "google",
      providers: [/^mvp(02|04|06|08|10):/.test(which) ? "github" : "google"],
    },
    user_metadata: {},
    created_at: profile.created_at,
    last_sign_in_at: new Date().toISOString(),
  };
}
function session(which: string) {
  const encode = (value: object) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return {
    access_token: `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: user(which).id, aud: "authenticated", role: "authenticated", exp: Math.floor(Date.now() / 1000) + 3600, iat: Math.floor(Date.now() / 1000), mock_case: which, amr: [{ method: "oauth", timestamp: Math.floor(Date.now() / 1000) }] })}.c3ludGhldGlj`,
    refresh_token: `synthetic-${which}`,
    token_type: "bearer",
    expires_in: 3600,
    user: user(which),
  };
}
function tokenCase(request: Request) {
  try {
    return JSON.parse(
      Buffer.from(
        request.headers.get("authorization")!.split(".")[1],
        "base64url",
      ).toString(),
    ).mock_case as string;
  } catch {
    return "normal";
  }
}
const stores = new Map<string, ReturnType<typeof createMockFetch>>();
const server = createServer(async (req, res) => {
  try {
    const origin = "http://127.0.0.1:8001";
    const url = new URL(req.url!, origin);
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const headers = new Headers();
    for (const [name, value] of Object.entries(req.headers))
      if (value)
        headers.set(name, Array.isArray(value) ? value.join(",") : value);
    const request = new Request(url, {
      method: req.method,
      headers,
      ...(chunks.length ? { body: Buffer.concat(chunks) } : {}),
    });
    let response: Response;
    if (
      url.pathname === "/auth/v1/authorize" &&
      (new URL(url.searchParams.get("redirect_to")!).pathname ===
        "/auth/reauth/callback" ||
        new URL(url.searchParams.get("redirect_to")!).searchParams.get("next") === "/deletion-status")
    ) {
      const chunks = (req.headers.cookie ?? "")
        .split(";")
        .map((c) => c.trim().split("="));
      const cookie = chunks
        .filter(([name]) => /^sb-.*-auth-token(?:\.\d+)?$/.test(name))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([, value]) => decodeURIComponent(value))
        .join("");
      const stored = JSON.parse(
        cookie.startsWith("base64-")
          ? Buffer.from(cookie.slice(7), "base64url").toString()
          : cookie,
      );
      const current = tokenCase(
        new Request(origin, {
          headers: { authorization: `Bearer ${stored.access_token}` },
        }),
      );
      const success = new URL(url.searchParams.get("redirect_to")!);
      success.searchParams.set(
        "code",
        `reauth:${Buffer.from(current).toString("base64url")}`,
      );
      const failure = new URL(url.searchParams.get("redirect_to")!);
      failure.searchParams.set("error", "access_denied");
      const other = new URL(url.searchParams.get("redirect_to")!);
      other.searchParams.set("code", `second:${crypto.randomUUID()}`);
      response = new Response(
        `<html lang="ja"><body><h1>テスト専用 本人確認</h1><a href="${success}">同じアカウントで本人確認を完了</a><p><a href="${failure}">本人確認をキャンセル</a></p><p><a href="${other}">別のアカウントを選ぶ</a></p></body></html>`,
        { headers: { "Content-Type": "text/html; charset=utf-8" } },
      );
    } else if (url.pathname === "/auth/v1/authorize") {
      const callback = url.searchParams.get("redirect_to")!;
      response = new Response(
        `<html lang="ja"><body><h1>テスト専用 OAuth</h1>${[
          ...Array.from(
            { length: 10 },
            (_, index) => `mvp${String(index + 1).padStart(2, "0")}`,
          ),
          "normal",
          "activity",
          "activity-empty",
          "existing",
          "second",
          "forbidden",
          "inactive",
          "unavailable",
          "expired",
          "failure",
          "cancel",
        ]
          .map((which) => {
            const to = new URL(callback);
            to.searchParams.set(
              which === "cancel" ? "error" : "code",
              `${which}:${crypto.randomUUID()}`,
            );
            return `<p><a href="${to.toString().replaceAll("&", "&amp;")}">${which}</a></p>`;
          })
          .join("")}</body></html>`,
        { headers: { "Content-Type": "text/html; charset=utf-8" } },
      );
    } else if (url.pathname === "/auth/v1/token") {
      const body = await request.json();
      const raw =
        body.auth_code ?? String(body.refresh_token).replace("synthetic-", "");
      const which = raw.startsWith("reauth:")
        ? Buffer.from(raw.slice(7), "base64url").toString()
        : raw;
      response = which.startsWith("failure")
        ? Response.json(
            { error: "invalid_grant", error_description: "Synthetic failure" },
            { status: 400 },
          )
        : Response.json(
            session(
              which.startsWith("expired") && body.refresh_token
                ? "normal"
                : which,
            ),
          );
    } else if (url.pathname === "/auth/v1/user")
      response = Response.json(user(tokenCase(request)));
    else if (url.pathname === "/auth/v1/logout")
      response = new Response(null, { status: 204 });
    else if (url.pathname === "/health")
      response = Response.json({ status: "ok", service: "api", mode: "mock" });
    else {
      const which = tokenCase(request);
      const mode = which.split(":")[0];
      if (mode === "forbidden") response = mockError(403);
      else if (mode === "inactive")
        response = mockError(403, "ACCOUNT_INACTIVE");
      else if (mode === "unavailable") response = mockError(503, "SERVICE_UNAVAILABLE");
      else if (mode === "expired") response = mockError(401);
      else {
        if (!stores.has(which)) {
          const store = createMockFetch(
            mode === "activity-empty" ? "empty" : "normal",
            {
              seedAgent: mode.startsWith("activity"),
              activity: mode === "activity",
            },
          );
          stores.set(which, store);
          await store(
            new Request(`${origin}/api/v1/users/me`, {
              method: "PATCH",
              headers: {
                authorization: "Bearer synthetic",
                "content-type": "application/json",
              },
              body: JSON.stringify({
                display_name: which.startsWith("second")
                  ? "別のユーザー"
                  : "招待ユーザー",
              }),
            }),
          );
        }
        response = await stores.get(which)!(request);
        if (url.pathname === "/api/v1/users/me" && response.ok)
          response = Response.json({
            ...(await response.json()),
            id: user(which).id,
            onboarded_at: mode === "existing" ? profile.created_at : null,
          });
      }
    }
    res.writeHead(response.status, {
      ...Object.fromEntries(response.headers),
      "cache-control": "no-store",
      "access-control-allow-origin": "http://127.0.0.1:3000",
    });
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(500);
    res.end();
  }
});
server.listen(8001, "127.0.0.1");
