// Test process only. There is no authentication bypass in application code.
import { createServer } from "node:http";
import { createMockFetch, mockError, profile } from "@alter/api-client/mock";

function user(which: string) {
  return {
    id: which.startsWith("second")
      ? "00000000-0000-4000-8000-000000000002"
      : profile.id,
    aud: "authenticated",
    role: "authenticated",
    email: "synthetic@example.test",
    app_metadata: { provider: "google", providers: ["google"] },
    user_metadata: {},
    created_at: profile.created_at,
  };
}
function session(which: string) {
  const encode = (value: object) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return {
    access_token: `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: user(which).id, aud: "authenticated", role: "authenticated", exp: Math.floor(Date.now() / 1000) + 3600, iat: Math.floor(Date.now() / 1000), mock_case: which })}.c3ludGhldGlj`,
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
    if (url.pathname === "/auth/v1/authorize") {
      const callback = url.searchParams.get("redirect_to")!;
      response = new Response(
        `<html lang="ja"><body><h1>テスト専用 OAuth</h1>${[
          "normal",
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
      const which =
        body.auth_code ?? String(body.refresh_token).replace("synthetic-", "");
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
      else if (mode === "unavailable") response = mockError(503);
      else if (mode === "expired") response = mockError(401);
      else if (url.pathname === "/api/v1/users/me")
        response = Response.json({
          ...profile,
          id: user(which).id,
          display_name: which.startsWith("second")
            ? "別のユーザー"
            : "招待ユーザー",
          onboarded_at: mode === "existing" ? profile.created_at : null,
        });
      else {
        if (!stores.has(which)) stores.set(which, createMockFetch());
        response = await stores.get(which)!(request);
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
