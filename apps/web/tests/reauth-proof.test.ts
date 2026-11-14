// @vitest-environment node
import { expect, test } from "vitest";
import { recentOAuth, tokenHash, validProof } from "../lib/reauth-proof";
const now = 1800000000;
function token(method = "oauth", timestamp = now) {
  return `header.${Buffer.from(JSON.stringify({ sub: "owner", exp: now + 3600, iat: now, amr: [{ method, timestamp }] })).toString("base64url")}.signature`;
}
test("deletion requires fresh OAuth rather than a refreshed token's iat", () => {
  expect(recentOAuth(token(), "owner", now)).toBe(true);
  expect(recentOAuth(token("token_refresh"), "owner", now)).toBe(false);
  expect(recentOAuth(token("oauth", now - 301), "owner", now)).toBe(false);
  expect(recentOAuth(token("oauth", now + 31), "owner", now)).toBe(false);
  expect(recentOAuth(token(), "other", now)).toBe(false);
});
test("UI receipt is short lived and bound to verified user and current access token", () => {
  const access = token();
  const proof = JSON.stringify({
    userId: "owner",
    tokenHash: tokenHash(access),
    expiresAt: now + 300,
  });
  expect(validProof(proof, access, "owner", now)).toBe(true);
  expect(validProof(proof, token("oauth", now + 1), "owner", now)).toBe(false);
  expect(validProof(proof, access, "other", now)).toBe(false);
  expect(validProof(proof, access, "owner", now + 301)).toBe(false);
  expect(validProof("broken", access, "owner", now)).toBe(false);
});
