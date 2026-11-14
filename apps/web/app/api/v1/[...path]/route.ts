import { type NextRequest } from "next/server";
import { appOrigin } from "../../../../lib/origin";
import { serverApi } from "../../../../lib/api-server";
import { authenticatedFetch } from "@alter/api-client/preview";

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const pathname = path.join("/");
  // Fixed allowlist prevents using this route as an arbitrary authenticated proxy.
  if (
    !/^(users\/me|agent-icons|conversations(?:\/[a-f\d-]{36})?|notifications(?:\/[a-f\d-]{36}\/read)?|discord\/(?:connection|guilds)|agents(?:\/me|\/[a-f\d-]{36}(?:\/blocks\/[a-f\d-]{36}|\/settings|\/profile(?:\/private|\/revisions|\/approve|\/suggest)?)?)?)$/.test(
      pathname,
    )
  )
    return new Response(null, { status: 404 });
  if (
    request.method !== "GET" &&
    request.headers.get("origin") !== appOrigin(request)
  )
    return new Response(null, { status: 403 });
  const { supabase } = await serverApi();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user)
    return Response.json(
      {
        error: {
          code: "UNAUTHENTICATED",
          message: "ログインしてください。",
          request_id: crypto.randomUUID(),
        },
      },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  const transport = authenticatedFetch({
    accessToken: async () =>
      (await supabase.auth.getSession()).data.session?.access_token ?? null,
    refreshToken: async () =>
      (await supabase.auth.refreshSession()).data.session?.access_token ?? null,
    onUnauthenticated: () => {},
  });
  const upstream = new URL(
    `/api/v1/${pathname}`,
    process.env.API_URL ??
      process.env.NEXT_PUBLIC_API_URL ??
      "http://localhost:8000",
  );
  upstream.search = request.nextUrl.search;
  const isIcon = pathname === "agent-icons";
  if (isIcon && request.method !== "POST") return new Response(null, { status: 405 });
  const contentType = isIcon ? request.headers.get("Content-Type") ?? "" : "application/json";
  const headers = new Headers({ "Content-Type": contentType });
  const idempotency = request.headers.get("Idempotency-Key");
  if (idempotency) headers.set("Idempotency-Key", idempotency);
  try {
    let body: BodyInit | undefined;
    if (isIcon) {
      const invalid = () => Response.json({error: {code: "VALIDATION_ERROR", message: "画像を確認してください。", request_id: crypto.randomUUID()}}, {status: 422, headers: {"Cache-Control": "no-store"}});
      if (!["image/png", "image/jpeg", "image/webp"].includes(contentType.split(";", 1)[0].toLowerCase())) return invalid();
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader) {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 2097152) { await reader.cancel(); return invalid(); }
          chunks.push(value);
        }
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      body = bytes.buffer;
    } else if (request.method !== "GET") body = await request.text();
    const response = await transport(upstream, {
      method: request.method,
      headers,
      ...(body === undefined ? {} : { body }),
    });
    return new Response(response.status === 204 ? null : response.body, {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "private, no-store",
        ...(response.headers.get("Retry-After")
          ? { "Retry-After": response.headers.get("Retry-After")! }
          : {}),
        ...(response.headers.get("X-Request-ID")
          ? { "X-Request-ID": response.headers.get("X-Request-ID")! }
          : {}),
      },
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "SERVICE_UNAVAILABLE",
          message: "接続できません。再試行してください。",
          request_id: crypto.randomUUID(),
        },
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
export { forward as GET, forward as POST, forward as PATCH, forward as PUT, forward as DELETE };
