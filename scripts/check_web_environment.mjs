import { pathToFileURL } from "node:url";

const publicNames = new Set([
  "NEXT_PUBLIC_SITE_URL",
  "NEXT_PUBLIC_API_URL",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
]);
function origin(value) {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      url.hostname === "localhost" ||
      /^127\./.test(url.hostname)
    )
      return null;
    return url.origin;
  } catch {
    return null;
  }
}
export function checkWebEnvironment(env) {
  if (!["preview", "production"].includes(env.VERCEL_ENV)) return [];
  const errors = [];
  for (const name of [
    "NEXT_PUBLIC_SITE_URL",
    "NEXT_PUBLIC_SUPABASE_URL",
    "API_URL",
  ]) {
    if (!origin(env[name])) errors.push(`${name}: HTTPS origin required`);
  }
  if (env.NEXT_PUBLIC_API_URL && env.NEXT_PUBLIC_API_URL !== env.API_URL)
    errors.push("NEXT_PUBLIC_API_URL: must match API_URL");
  for (const name of Object.keys(env)) {
    if (name.startsWith("NEXT_PUBLIC_") && !publicNames.has(name))
      errors.push(`${name}: unexpected public variable`);
    if (
      /^(SUPABASE_(SECRET_KEY|SERVICE_ROLE_KEY|DB_URL)|DATABASE_URL|DISCORD_(CLIENT_SECRET|BOT_TOKEN)|LLM_API_KEY|PROFILE_ENCRYPTION_KEY.*)$/.test(
        name,
      ) &&
      env[name]
    )
      errors.push(`${name}: do not install backend credentials in Web`);
  }
  const key = env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";
  let safe = /^sb_publishable_[A-Za-z0-9_-]+$/.test(key);
  if (key.split(".").length === 3) {
    try {
      safe =
        JSON.parse(Buffer.from(key.split(".")[1], "base64url")).role === "anon";
    } catch {
      safe = false;
    }
  }
  if (!safe)
    errors.push(
      "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: publishable/anon key required",
    );
  const other =
    env[
      env.VERCEL_ENV === "preview"
        ? "ALTER_PRODUCTION_SUPABASE_URL"
        : "ALTER_PREVIEW_SUPABASE_URL"
    ];
  if (!origin(other) || origin(other) === origin(env.NEXT_PUBLIC_SUPABASE_URL))
    errors.push(
      "Supabase: distinct Preview and Production project origins required",
    );
  const allowed = (env.ALTER_REGISTERED_WEB_ORIGINS ?? "")
    .split(",")
    .map((value) => value.trim());
  if (
    !allowed.length ||
    allowed.some((value) => !origin(value)) ||
    !allowed.includes(origin(env.NEXT_PUBLIC_SITE_URL))
  )
    errors.push(
      "ALTER_REGISTERED_WEB_ORIGINS: exact registered HTTPS origins required",
    );
  if (
    env.VERCEL_ENV === "production" &&
    (!/^[a-f0-9]{40}$/.test(env.VERCEL_GIT_COMMIT_SHA ?? "") ||
      env.ALTER_APPROVED_PRODUCTION_COMMIT !== env.VERCEL_GIT_COMMIT_SHA)
  )
    errors.push(
      "Production: approve the current commit after preproduction acceptance",
    );
  return errors;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const errors = checkWebEnvironment(process.env);
  for (const error of errors) console.error(error); // Names/reasons only; never print values.
  process.exitCode = errors.length ? 1 : 0;
}
