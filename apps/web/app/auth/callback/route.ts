import { NextResponse, type NextRequest } from "next/server";
import { appOrigin } from "../../../lib/origin";
import { serverApi } from "../../../lib/api-server";
import { safeReturnPath } from "../../../lib/auth-policy";
import { unwrap, ApiError } from "@alter/api-client/preview";
export async function GET(request: NextRequest) {
  const destination = (path: string) => {
    const response = NextResponse.redirect(new URL(path, appOrigin(request)));
    response.headers.set("Cache-Control", "private, no-store"); return response;
  };
  const code = request.nextUrl.searchParams.get("code");
  if (!code || request.nextUrl.searchParams.has("error")) return destination("/login?error=oauth");
  try {
    const { client, apiClient, supabase } = await serverApi();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return destination("/login?error=oauth");
    if (safeReturnPath(request.nextUrl.searchParams.get("next")) === "/deletion-status") {
      // This exception reads only the verified owner's minimal deletion status.
      const { data, error: userError } = await supabase.auth.getUser();
      if (userError || !data.user) return destination("/login?error=expired");
      await unwrap(await apiClient.GET("/api/v1/users/me/deletion", { cache: "no-store" }));
      return destination("/deletion-status");
    }
    let profile;
    try {
      profile = await unwrap(await client.GET("/api/v1/users/me"));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403 &&
          ["INVITATION_REQUIRED", "ACCOUNT_INACTIVE"].includes(cause.detail.code)) {
        // End only this browser's session; preserve the original admission error.
        await supabase.auth.signOut({ scope: "local" }).catch(() => undefined);
      }
      throw cause;
    }
    if (!profile.onboarded_at) return destination("/onboarding");
    return destination(safeReturnPath(request.nextUrl.searchParams.get("next")));
  } catch (cause) {
    const error = cause instanceof ApiError ? cause.status === 401 ? "expired" : cause.detail.code : "AUTHORIZATION_UNAVAILABLE";
    return destination(`/login?error=${encodeURIComponent(error)}`);
  }
}
