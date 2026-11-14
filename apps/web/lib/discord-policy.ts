import { createHash } from "node:crypto";
export const discordStateCookie = "alter-discord-state";
export function stateHash(state: string) {
  return createHash("sha256").update(state).digest("hex");
}
export function discordAuthorization(value: string, origin: string) {
  const url = new URL(value);
  const state = url.searchParams.get("state");
  const target = url.searchParams.get("redirect_uri");
  if (
    url.origin !== "https://discord.com" ||
    url.pathname !== "/oauth2/authorize" ||
    url.username ||
    url.password ||
    url.hash ||
    ["state", "scope", "response_type", "redirect_uri", "client_id"].some(
      (key) => url.searchParams.getAll(key).length !== 1,
    ) ||
    !/^\d+$/.test(url.searchParams.get("client_id") ?? "") ||
    url.searchParams.getAll("redirect_uri").length !== 1 ||
    !state ||
    state.length < 16 ||
    state.length > 256 ||
    target !== new URL("/auth/discord/callback", origin).toString() ||
    url.searchParams.get("response_type") !== "code" ||
    url.searchParams.get("scope") !== "identify"
  )
    throw new Error("Invalid Discord authorization URL");
  return { url: url.toString(), state };
}
export function validDiscordState(
  value: string | undefined,
  state: string,
  userId: string,
  now = Date.now(),
) {
  try {
    const pending = JSON.parse(value ?? "null");
    return (
      pending?.userId === userId &&
      pending.hash === stateHash(state) &&
      pending.expires > now &&
      pending.expires <= now + 630_000
    );
  } catch {
    return false;
  }
}
