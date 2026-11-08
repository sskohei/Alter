"use server";
import { redirect } from "next/navigation";
import { cookies, headers } from "next/headers";
import { serverSupabase } from "../../lib/supabase/server";
import { safeReturnPath } from "../../lib/auth-policy";

export async function login(form: FormData) {
  if (form.get("consent") !== "yes") redirect("/login?error=consent");
  const provider = form.get("provider");
  if (provider !== "google" && provider !== "github")
    redirect("/login?error=oauth");
  if (
    !process.env.NEXT_PUBLIC_SUPABASE_URL ||
    !process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  )
    redirect("/login?error=configuration");
  const origin =
    process.env.NEXT_PUBLIC_SITE_URL ??
    (await headers()).get("origin") ??
    "http://127.0.0.1:3000";
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", safeReturnPath(form.get("next")));
  const supabase = await serverSupabase();
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider,
    options: { redirectTo: callback.toString() },
  });
  if (error || !data.url) redirect("/login?error=oauth");
  redirect(data.url);
}

export async function logout() {
  const supabase = await serverSupabase();
  const { error } = await supabase.auth.signOut();
  const store = await cookies();
  for (const name of [
    "alter-delete-challenge",
    "alter-delete-proof",
    "alter-discord-state",
  ])
    store.delete(name);
  if (error) redirect("/login?error=oauth");
  // A full navigation destroys client state; no persistent Query cache is used.
  redirect("/login");
}
