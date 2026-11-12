import { type NextRequest } from "next/server";
import { appOrigin } from "../../../../lib/origin";
import { serverApi } from "../../../../lib/api-server";
import { authenticatedFetch } from "@alter/api-client/preview";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const pathname = path.join("/");
  // Fixed allowlist prevents using this route as an arbitrary authenticated proxy.
  if (!/^(users\/me|agents(?:\/me|\/[a-f\d-]{36}(?:\/profile(?:\/private|\/revisions|\/approve|\/suggest)?)?)?)$/.test(pathname)) return new Response(null, { status: 404 });
  if (request.method !== "GET" && request.headers.get("origin") !== appOrigin(request)) return new Response(null, { status: 403 });
  const { supabase } = await serverApi();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) return Response.json({ error: { code: "UNAUTHENTICATED", message: "ログインしてください。", request_id: crypto.randomUUID() } }, { status: 401, headers: { "Cache-Control": "no-store" } });
  const transport = authenticatedFetch({
    accessToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
    refreshToken: async () => (await supabase.auth.refreshSession()).data.session?.access_token ?? null,
    onUnauthenticated: () => {},
  });
  const upstream = new URL(`/api/v1/${pathname}`, process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
  upstream.search = request.nextUrl.search;
  const headers = new Headers({ "Content-Type": "application/json" });
  const idempotency = request.headers.get("Idempotency-Key");
  if (idempotency) headers.set("Idempotency-Key", idempotency);
  try {
    const response = await transport(upstream, { method: request.method, headers, ...(request.method === "GET" ? {} : { body: await request.text() }) });
    return new Response(response.body, { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store", ...(response.headers.get("X-Request-ID") ? { "X-Request-ID": response.headers.get("X-Request-ID")! } : {}) } });
  } catch { return Response.json({ error: { code: "SERVICE_UNAVAILABLE", message: "接続できません。再試行してください。", request_id: crypto.randomUUID() } }, { status: 503, headers: { "Cache-Control": "no-store" } }); }
}
export { forward as GET, forward as POST, forward as PATCH };
