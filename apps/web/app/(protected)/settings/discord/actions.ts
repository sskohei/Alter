"use server";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { serverApi } from "../../../../lib/api-server";
import {
  discordAuthorization,
  discordStateCookie,
  stateHash,
} from "../../../../lib/discord-policy";
import { ApiError, unwrap } from "@alter/api-client";
export async function beginDiscordLink() {
  const { apiClient: client, supabase } = await serverApi();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) redirect("/login?error=expired");
  let destination: string;
  try {
    const origin =
      process.env.NEXT_PUBLIC_SITE_URL ??
      (await headers()).get("origin") ??
      "http://127.0.0.1:3000";
    const started = await unwrap(
      await client.POST("/api/v1/discord/link/start"),
    );
    const authorized = discordAuthorization(started.url, origin);
    const expires = Date.parse(started.state_expires_at);
    if (
      !Number.isFinite(expires) ||
      expires <= Date.now() ||
      expires > Date.now() + 630_000
    )
      throw new Error("Invalid state expiry");
    (await cookies()).set(
      discordStateCookie,
      JSON.stringify({
        userId: data.user.id,
        hash: stateHash(authorized.state),
        expires,
      }),
      {
        httpOnly: true,
        sameSite: "lax",
        secure: origin.startsWith("https:"),
        path: "/",
        maxAge: 600,
      },
    );
    destination = authorized.url;
  } catch (cause) {
    const reason = cause instanceof ApiError && cause.status === 409 ? "conflict" : "start";
    redirect(`/settings/discord?error=${reason}`);
  }
  redirect(destination);
}
