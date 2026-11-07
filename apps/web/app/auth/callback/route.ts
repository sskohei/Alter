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
    const { client, supabase } = await serverApi();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return destination("/login?error=oauth");
    const profile = await unwrap(await client.GET("/api/v1/users/me"));
    if (!profile.onboarded_at) return destination("/onboarding");
    return destination(safeReturnPath(request.nextUrl.searchParams.get("next")));
  } catch (cause) {
    const error = cause instanceof ApiError ? cause.status === 401 ? "expired" : cause.detail.code : "AUTHORIZATION_UNAVAILABLE";
    return destination(`/login?error=${encodeURIComponent(error)}`);
  }
}
