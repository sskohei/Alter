import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import { ApiError, unwrap } from "@alter/api-client";
import { serverApi } from "../../../../lib/api-server";
import { appOrigin } from "../../../../lib/origin";
import {
  discordStateCookie,
  validDiscordState,
} from "../../../../lib/discord-policy";
export async function GET(request: NextRequest) {
  const store = await cookies();
  const pending = store.get(discordStateCookie)?.value;
  store.delete(discordStateCookie);
  const destination = (error?: string) => {
    const response = NextResponse.redirect(
      new URL(
        `/settings/discord${error ? `?error=${error}` : "?linked=1"}`,
        appOrigin(request),
      ),
    );
    response.headers.set("Cache-Control", "private, no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  };
  if (request.nextUrl.searchParams.has("error")) return destination("cancel");
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  if (
    !code ||
    !state ||
    request.nextUrl.searchParams.getAll("code").length !== 1 ||
    request.nextUrl.searchParams.getAll("state").length !== 1 ||
    code.length > 2048
  )
    return destination("state");
  try {
    const { apiClient: client, supabase } = await serverApi();
    const { data, error } = await supabase.auth.getUser();
    if (error || !data.user || !validDiscordState(pending, state, data.user.id))
      return destination("state");
    await unwrap(
      await client.POST("/api/v1/discord/link/complete", {
        body: { code, state },
      }),
    );
    return destination();
  } catch (cause) {
    return destination(
      cause instanceof ApiError && cause.status === 409
        ? "conflict"
        : cause instanceof ApiError && cause.status === 400
          ? "state"
          : "unavailable",
    );
  }
}
