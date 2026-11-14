import { pathToFileURL } from "node:url";

export const requiredOperations = {
  "/api/v1/users/me": ["get", "patch"],
  "/api/v1/agents": ["post"],
  "/api/v1/agents/me": ["get"],
  "/api/v1/agents/{agent_id}/profile": ["get"],
  "/api/v1/agents/{agent_id}/profile/revisions": ["post"],
  "/api/v1/agents/{agent_id}/profile/approve": ["post"],
  "/api/v1/agents/{id}/settings": ["get", "patch"],
  "/api/v1/conversations": ["get"],
  "/api/v1/conversations/{id}": ["get"],
  "/api/v1/notifications": ["get"],
  "/api/v1/memories": ["get"],
  "/api/v1/memories/{id}": ["delete"],
  "/api/v1/relationships": ["get"],
  "/api/v1/preferences": ["get", "patch"],
  "/api/v1/discord/connection": ["get", "delete"],
  "/api/v1/discord/guilds": ["get"],
  "/api/v1/discord/link/start": ["post"],
  "/api/v1/discord/link/complete": ["post"],
  "/api/v1/users/me/deletion": ["get", "post"],
};

export async function smokeWeb({ web, api, fetcher = fetch }) {
  const errors = [];
  for (const value of [web, api]) {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.origin !== value ||
      url.username ||
      url.password
    )
      throw new Error("Exact HTTPS origins required");
  }
  async function get(base, path, init = {}) {
    return fetcher(`${base}${path}`, {
      ...init,
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    });
  }
  try {
    const login = await get(web, "/login");
    if (login.status !== 200) errors.push("login status");
    if (
      login.headers.get("referrer-policy") !== "same-origin" ||
      login.headers.get("x-content-type-options") !== "nosniff" ||
      login.headers.get("x-frame-options") !== "DENY"
    )
      errors.push("security headers");
    const protectedPage = await get(web, "/dashboard");
    if (!/no-store/.test(protectedPage.headers.get("cache-control") ?? ""))
      errors.push("protected page: no-store");
    const location = new URL(protectedPage.headers.get("location") ?? "/", web);
    if (
      ![303, 307].includes(protectedPage.status) ||
      location.origin !== web ||
      location.pathname !== "/login"
    )
      errors.push("protected page login redirect");
    for (const path of [
      "/api/v1/users/me",
      "/auth/callback?error=access_denied",
      "/auth/reauth/callback?error=access_denied",
      "/auth/discord/callback?error=access_denied",
    ]) {
      const response = await get(web, path);
      if (!/no-store/.test(response.headers.get("cache-control") ?? ""))
        errors.push(`${path.split("?")[0]}: no-store`);
      if (path.startsWith("/api") && response.status !== 401)
        errors.push("anonymous BFF rejection");
      if (path.startsWith("/auth")) {
        if (response.headers.get("referrer-policy") !== "no-referrer")
          errors.push("callback referrer policy");
        const target = new URL(response.headers.get("location") ?? "/", web);
        if (
          ![302, 303, 307].includes(response.status) ||
          target.origin !== web ||
          target.searchParams.has("code") ||
          target.searchParams.has("state")
        )
          errors.push("safe OAuth failure redirect");
      }
    }
    const headers = {
      Origin: web,
      "Access-Control-Request-Method": "GET",
      "Access-Control-Request-Headers": "authorization",
    };
    const cors = await get(api, "/api/v1/users/me", {
      method: "OPTIONS",
      headers,
    });
    if (
      !cors.ok ||
      cors.headers.get("access-control-allow-origin") !== web ||
      !/(^|,)\s*origin\s*(,|$)/i.test(cors.headers.get("vary") ?? "")
    )
      errors.push("registered CORS origin");
    const denied = await get(api, "/api/v1/users/me", {
      method: "OPTIONS",
      headers: { ...headers, Origin: "https://unregistered.invalid" },
    });
    if (denied.headers.has("access-control-allow-origin"))
      errors.push("unregistered CORS origin accepted");
    const anonymous = await get(api, "/api/v1/users/me");
    if (anonymous.status !== 401) errors.push("anonymous API rejection");
    const schema = await get(api, "/openapi.json");
    const paths = schema.ok ? ((await schema.json()).paths ?? {}) : {};
    for (const [path, methods] of Object.entries(requiredOperations)) {
      for (const method of methods) {
        if (!paths[path]?.[method]?.responses)
          errors.push(`live contract missing: ${method.toUpperCase()} ${path}`);
      }
    }
  } catch {
    errors.push("request or response failed (inspect private service logs)");
  }
  return errors;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    const errors = await smokeWeb({
      web: process.env.ALTER_SMOKE_WEB_ORIGIN,
      api: process.env.ALTER_SMOKE_API_ORIGIN,
    });
    errors.forEach((error) => console.error(error));
    process.exitCode = errors.length ? 1 : 0;
  } catch {
    console.error(
      "Set ALTER_SMOKE_WEB_ORIGIN and ALTER_SMOKE_API_ORIGIN to exact HTTPS origins",
    );
    process.exitCode = 1;
  }
}
