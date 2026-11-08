"use server";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { serverApi } from "../../../../lib/api-server";
import {
  discordAuthorization,
  discordStateCookie,
  stateHash,
} from "../../../../lib/discord-policy";
import { unwrap } from "@alter/api-client/preview";
export async function beginDiscordLink() {
  const { client, supabase } = await serverApi();
  const { data } = await supabase.auth.getUser();
  if (!data.user) redirect("/login?error=expired");
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
  } catch {
    redirect("/settings/discord?error=start");
  }
  redirect(destination);
}
