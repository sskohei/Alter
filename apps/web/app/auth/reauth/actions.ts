"use server";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { serverSupabase } from "../../../lib/supabase/server";
import {
  challengeCookie,
  proofCookie,
  reauthLifetime,
} from "../../../lib/reauth-proof";
export async function reauthenticate() {
  const supabase = await serverSupabase();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) redirect("/login?error=expired");
  const provider = data.user.app_metadata.provider;
  if (provider !== "google" && provider !== "github")
    redirect("/settings/account?error=reauth");
  const store = await cookies();
  store.delete(proofCookie);
  const origin =
    process.env.NEXT_PUBLIC_SITE_URL ??
    (await headers()).get("origin") ??
    "http://127.0.0.1:3000";
  store.set(
    challengeCookie,
    JSON.stringify({
      userId: data.user.id,
      startedAt: Date.now() / 1000,
      nonce: crypto.randomUUID(),
    }),
    {
      httpOnly: true,
      sameSite: "lax",
      secure: new URL(origin).protocol === "https:",
      path: "/",
      maxAge: reauthLifetime,
    },
  );
  const { data: oauth, error: oauthError } =
    await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: new URL("/auth/reauth/callback", origin).toString(),
        ...(provider === "google"
          ? { queryParams: { prompt: "select_account" } }
          : {}),
      },
    });
  if (oauthError || !oauth.url) redirect("/settings/account?error=reauth");
  redirect(oauth.url);
}
