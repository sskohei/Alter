import type { NextRequest } from "next/server";
export function appOrigin(request: NextRequest) {
  // Pin in deployed environments; Host fallback supports local Next start.
  return process.env.NEXT_PUBLIC_SITE_URL ?? `${request.nextUrl.protocol}//${request.headers.get("host") ?? request.nextUrl.host}`;
}
