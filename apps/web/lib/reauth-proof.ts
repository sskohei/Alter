import { createHash } from "node:crypto";
export const challengeCookie = "alter-delete-challenge";
export const proofCookie = "alter-delete-proof";
export const reauthLifetime = 300;
export function tokenHash(token: string) {
  return createHash("sha256").update(token).digest("hex");
}
// Only call after Auth getUser has verified this token; this is not a JWT verifier.
export function recentOAuth(
  token: string,
  userId: string,
  now = Date.now() / 1000,
  startedAt = now - reauthLifetime,
) {
  try {
    const claims = JSON.parse(
      Buffer.from(token.split(".")[1], "base64url").toString(),
    );
    return (
      claims.sub === userId &&
      claims.exp > now &&
      Array.isArray(claims.amr) &&
      claims.amr.some(
        (method: { method?: unknown; timestamp?: unknown }) =>
          method.method === "oauth" &&
          typeof method.timestamp === "number" &&
          method.timestamp >= Math.max(now - reauthLifetime, startedAt - 30) &&
          method.timestamp <= now + 30,
      )
    );
  } catch {
    return false;
  }
}
export function validProof(
  value: string | undefined,
  token: string,
  userId: string,
  now = Date.now() / 1000,
) {
  try {
    const proof = JSON.parse(value ?? "null");
    return (
      proof?.userId === userId &&
      proof.tokenHash === tokenHash(token) &&
      proof.expiresAt > now &&
      proof.expiresAt <= now + reauthLifetime + 30 &&
      recentOAuth(token, userId, now)
    );
  } catch {
    return false;
  }
}
