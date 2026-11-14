import type { components } from "./schema";
export function createDiscordMock(withGuild: boolean) {
  let connected = false;
  let revoked = false;
  const states = new Map<string, { expires: number; used: boolean }>();
  const connection = (): components["schemas"]["DiscordConnection"] => ({
    connected,
    discord_user_id: connected ? "333333333333333333" : null,
  });
  return async (request: Request): Promise<Response | null> => {
    const path = new URL(request.url).pathname;
    if (path === "/api/v1/discord/connection") {
      if (request.method === "DELETE") {
        connected = false;
        revoked = true;
        states.clear();
        return new Response(null, { status: 204 });
      }
      return Response.json(connection());
    }
    if (path === "/api/v1/discord/guilds")
      return Response.json(
        withGuild
          ? [
              {
                guild_id: "111111111111111111",
                guild_name: "テスト交流サーバー",
                channel_id: "222222222222222222",
                channel_name: "ai交流",
                guild_enabled: true,
                consented_at: revoked ? null : "2026-11-07T00:00:00Z",
                revoked_at: revoked ? new Date().toISOString() : null,
              },
            ]
          : [],
      );
    if (path === "/api/v1/discord/link/start" && request.method === "POST") {
      if (connected) return new Response(null, { status: 409 });
      const state = crypto.randomUUID();
      const expires = Date.now() + 600_000;
      states.set(state, { expires, used: false });
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
      });
    }
    if (path === "/api/v1/discord/link/complete" && request.method === "POST") {
      const { state, code } = await request.json();
      const pending = states.get(state);
      if (
        !pending ||
        pending.used ||
        pending.expires < Date.now() ||
        code === "expired"
      )
        return new Response(null, { status: 400 });
      pending.used = true;
      if (code === "conflict") return new Response(null, { status: 409 });
      if (code !== "synthetic-code") return new Response(null, { status: 400 });
      connected = true;
      return Response.json(connection());
    }
    return null;
  };
}
