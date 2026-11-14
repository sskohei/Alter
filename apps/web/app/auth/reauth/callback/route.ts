import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { serverSupabase } from "../../../../lib/supabase/server";
import { appOrigin } from "../../../../lib/origin";
import {
  challengeCookie,
  proofCookie,
  reauthLifetime,
  recentOAuth,
  tokenHash,
} from "../../../../lib/reauth-proof";
export async function GET(request: NextRequest) {
  const store = await cookies();
  const value = store.get(challengeCookie)?.value;
  store.delete(challengeCookie);
  store.delete(proofCookie);
  const destination = (path: string) => {
    const response = NextResponse.redirect(new URL(path, appOrigin(request)));
    response.headers.set("Cache-Control", "private, no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  };
  try {
    const challenge = JSON.parse(value ?? "null");
    const now = Date.now() / 1000;
    const code = request.nextUrl.searchParams.get("code");
    if (
      !challenge ||
      typeof challenge.userId !== "string" ||
      !Number.isFinite(challenge.startedAt) ||
      challenge.startedAt > now + 30 ||
      now - challenge.startedAt > reauthLifetime ||
      !code ||
      request.nextUrl.searchParams.has("error")
    )
      return destination("/settings/account?error=reauth");
    const supabase = await serverSupabase();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return destination("/settings/account?error=reauth");
    const { data } = await supabase.auth.getUser();
    const token = (await supabase.auth.getSession()).data.session?.access_token;
    if (!data.user || data.user.id !== challenge.userId) {
      await supabase.auth.signOut();
      return destination("/login?error=reauth");
    }
    if (!token || !recentOAuth(token, data.user.id, now, challenge.startedAt))
      return destination("/settings/account?error=reauth");
    store.set(
      proofCookie,
      JSON.stringify({
        userId: data.user.id,
        tokenHash: tokenHash(token),
        expiresAt: now + reauthLifetime,
      }),
      {
        httpOnly: true,
        sameSite: "lax",
        secure: appOrigin(request).startsWith("https:"),
        path: "/",
        maxAge: reauthLifetime,
      },
    );
    return destination("/settings/account");
  } catch {
    return destination("/settings/account?error=reauth");
  }
}
