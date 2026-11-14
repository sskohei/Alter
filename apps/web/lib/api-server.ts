import { createApiClient } from "@alter/api-client";
import { cache } from "react";
import { redirect } from "next/navigation";
import { createPreviewClient, authenticatedFetch, unwrap, ApiError } from "@alter/api-client/preview";
import { serverSupabase } from "./supabase/server";

export async function serverApi() {
  const supabase = await serverSupabase();
  const fetcher = authenticatedFetch({
    accessToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
    refreshToken: async () => (await supabase.auth.refreshSession()).data.session?.access_token ?? null,
    onUnauthenticated: () => {},
  });
  const client = createPreviewClient(process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000", fetcher);
  const apiClient = createApiClient(process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000", fetcher);
  return { client, apiClient, supabase };
}

// Per-render memoization; never persist profiles in a shared Next cache.
export const requireProfile = cache(async () => {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) redirect("/login?error=configuration");
  const { apiClient, supabase } = await serverApi();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) redirect("/login?error=expired");
  try {
    return await unwrap(await apiClient.GET("/api/v1/users/me"));
  } catch (cause) {
    if (cause instanceof ApiError) {
      if (cause.status === 401) redirect("/login?error=expired");
      if (cause.status === 403 || cause.status === 503) redirect(`/login?error=${encodeURIComponent(cause.detail.code)}`);
    }
    redirect("/login?error=AUTHORIZATION_UNAVAILABLE");
  }
});
