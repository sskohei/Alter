import type { components } from "./schema";

function failure(status: number, code: components["schemas"]["ErrorBody"]["code"]) {
  return Response.json(
    { error: { code, message: "Synthetic Discord failure", request_id: crypto.randomUUID() } } satisfies components["schemas"]["ErrorResponse"],
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export function createDiscordMock(withGuild: boolean) {
  let connected = false;
  const states = new Map<string, number>();
  const connection = (): components["schemas"]["DiscordConnection"] => ({
    connected,
    discord_user_id: connected ? "333333333333333333" : null,
  });
  return async (request: Request): Promise<Response | null> => {
    const path = new URL(request.url).pathname;
    if (!path.startsWith("/api/v1/discord/")) return null;
    if (path === "/api/v1/discord/connection") {
      if (request.method === "DELETE") {
        connected = false;
        states.clear();
        return new Response(null, { status: 204 });
      }
      if (request.method !== "GET") return failure(405, "METHOD_NOT_ALLOWED");
      return Response.json(connection());
    }
    if (path === "/api/v1/discord/guilds") {
      if (request.method !== "GET") return failure(405, "METHOD_NOT_ALLOWED");
      return Response.json(
        withGuild
          ? [
              {
                guild_id: "111111111111111111",
                guild_name: "テスト交流サーバー",
                channel_id: "222222222222222222",
                channel_name: "ai交流",
                guild_enabled: true,
                consented_at: "2026-11-07T00:00:00Z",
                revoked_at: "2026-11-08T00:00:00Z",
              },
              {
                guild_id: "555555555555555555",
                guild_name: "公開先未設定サーバー",
                channel_id: null,
                channel_name: null,
                guild_enabled: false,
                consented_at: "2026-11-07T00:00:00Z",
                revoked_at: "2026-11-08T00:00:00Z",
              },
            ] satisfies components["schemas"]["Guild"][]
          : [],
      );
    }
    if (path === "/api/v1/discord/link/start") {
      if (request.method !== "POST") return failure(405, "METHOD_NOT_ALLOWED");
      if (connected) return failure(409, "CONFLICT");
      const state = crypto.randomUUID();
      const expires = Date.now() + 600_000;
      states.set(state, expires);
      const url = new URL("https://discord.com/oauth2/authorize");
      url.search = new URLSearchParams({
        client_id: "444444444444444444",
        response_type: "code",
        scope: "identify",
        state,
        redirect_uri: "http://127.0.0.1:3000/auth/discord/callback",
      }).toString();
      return Response.json({
        url: url.toString(),
        state_expires_at: new Date(expires).toISOString(),
      } satisfies components["schemas"]["DiscordLinkStart"]);
    }
    if (path === "/api/v1/discord/link/complete") {
      if (request.method !== "POST") return failure(405, "METHOD_NOT_ALLOWED");
      let body: components["schemas"]["DiscordLinkComplete"];
      try { body = await request.json(); } catch { return failure(422, "VALIDATION_ERROR"); }
      if (!body || typeof body.code !== "string" || !body.code || body.code.length > 2048 ||
          typeof body.state !== "string" || body.state.length < 16 || body.state.length > 256 ||
          Object.keys(body).some((key) => !["code", "state"].includes(key)))
        return failure(422, "VALIDATION_ERROR");
      const { state, code } = body;
      const expires = states.get(state);
      states.delete(state);
      if (!expires || expires <= Date.now() || code === "expired")
        return failure(400, "OAUTH_STATE_INVALID");
      if (connected || code === "conflict") return failure(409, "CONFLICT");
      if (code !== "synthetic-code") return failure(400, "OAUTH_STATE_INVALID");
      connected = true;
      return Response.json(connection());
    }
    return null;
  };
}
