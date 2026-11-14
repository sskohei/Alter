// @vitest-environment node
import { expect, test } from "vitest";
import {
  discordAuthorization,
  stateHash,
  validDiscordState,
} from "../lib/discord-policy";
const origin = "https://web.example.test";
const url = new URL("https://discord.com/oauth2/authorize");
url.search = new URLSearchParams({
  client_id: "123456789",
  response_type: "code",
  scope: "identify",
  state: "00000000-0000-4000-8000-000000000001",
  redirect_uri: `${origin}/auth/discord/callback`,
}).toString();
test("Discord authorize pins origin, callback and minimal scope", () => {
  expect(discordAuthorization(url.toString(), origin).state).toBe(
    url.searchParams.get("state"),
  );
});
for (const transform of [
  (u: URL) => {
    u.hostname = "evil.test";
  },
  (u: URL) => {
    u.protocol = "http:";
  },
  (u: URL) => {
    u.port = "444";
  },
  (u: URL) => {
    u.searchParams.set("redirect_uri", "https://evil.test/callback");
  },
  (u: URL) => {
    u.searchParams.append("scope", "bot");
  },
  (u: URL) => {
    u.searchParams.set("scope", "identify guilds");
  },
])
  test("rejects widened or redirected Discord authorization", () => {
    const changed = new URL(url);
    transform(changed);
    expect(() => discordAuthorization(changed.toString(), origin)).toThrow();
  });
test("state UI guard binds hash, owner and expiry", () => {
  const now = 1800000000000;
  const value = JSON.stringify({
    userId: "owner",
    hash: stateHash("state"),
    expires: now + 60_000,
  });
  expect(validDiscordState(value, "state", "owner", now)).toBe(true);
  expect(validDiscordState(value, "state", "other", now)).toBe(false);
  expect(validDiscordState(value, "other", "owner", now)).toBe(false);
  expect(validDiscordState(value, "state", "owner", now + 60_001)).toBe(false);
});
