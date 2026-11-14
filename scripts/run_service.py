"""Validated systemd entry point; secrets only arrive in the process environment."""

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

API_ONLY = {
    "SUPABASE_SECRET_KEY",
    "SUPABASE_STORAGE_SECRET_KEY",
    "PROFILE_ENCRYPTION_KEY_V1",
    "API_IDEMPOTENCY_HMAC_KEYS",
    "DISCORD_CLIENT_SECRET",
}


def validate(role: str, environment: str, env: dict[str, str]) -> None:
    if role not in {"api", "worker", "bot", "scheduler"}:
        raise ValueError("Unknown service")
    if environment not in {"preview", "production"}:
        raise ValueError("Unknown environment")
    if env.get("ALTER_ENVIRONMENT") != environment or env.get("ALTER_MODE") != "live":
        raise ValueError("Environment must match unit and be live")
    ref = env.get("ALTER_PROJECT_REF", "")
    other = env.get("ALTER_OTHER_PROJECT_REF", "")
    if not ref.isalnum() or not other.isalnum() or ref == other:
        raise ValueError("Distinct project refs required")
    if any("REPLACE" in value or "example.invalid" in value for value in env.values()):
        raise ValueError("Replace template values before starting")
    db = urlsplit(env.get("DATABASE_URL", ""))
    query = parse_qs(db.query)
    if (
        db.scheme != "postgresql+psycopg"
        or db.username != f"postgres.{ref}"
        or not (db.hostname or "").endswith(".pooler.supabase.com")
        or db.port != 6543
        or query.get("sslmode") != ["verify-full"]
        or not query.get("sslrootcert")
    ):
        raise ValueError("Expected project transaction pooler with verified TLS")
    forbidden = {"SUPABASE_DB_URL", "SUPABASE_ACCESS_TOKEN", "SUPABASE_DB_PASSWORD"}
    if role != "api":
        forbidden |= API_ONLY
    if role not in {"api", "worker"}:
        forbidden.add("ANTHROPIC_API_KEY")
    if role != "bot":
        forbidden.add("DISCORD_BOT_TOKEN")
    if any(env.get(key) for key in forbidden):
        raise ValueError("Unexpected credentials for service")
    if role == "api":
        if env.get("SUPABASE_URL") != f"https://{ref}.supabase.co":
            raise ValueError("Auth project mismatch")
        if env.get("SUPABASE_JWT_ISSUER") != f"https://{ref}.supabase.co/auth/v1":
            raise ValueError("JWT issuer mismatch")
        origin = env.get("WEB_URL", "")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query:
            raise ValueError("Web HTTPS Origin required")
        if env.get("CORS_ORIGINS") != origin:
            raise ValueError("One fixed Web Origin required")
    if role == "scheduler" and env.get("SCHEDULER_ENABLED") != "true":
        raise ValueError("Scheduler must be explicitly enabled")


def command(role: str, environment: str, root: Path) -> list[str]:
    binary = root / ".venv/bin"
    if role == "api":
        port = "8001" if environment == "preview" else "8000"
        return [
            str(binary / "uvicorn"),
            "alter_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
            "--workers",
            "1",
            "--no-access-log",
            "--log-level",
            "critical",
            "--no-proxy-headers",
            "--timeout-graceful-shutdown",
            "60",
        ]
    entry = {"worker": "alter-worker", "bot": "alter-bot", "scheduler": "alter-scheduler"}
    return [str(binary / entry[role])]


def main() -> int:
    try:
        role, environment = sys.argv[1:]
        root = Path(__file__).resolve().parents[1]
        if (root / ".env").exists():
            raise ValueError("Runtime checkout must not contain root .env")
        validate(role, environment, dict(os.environ))
        args = command(role, environment, root)
    except Exception:
        print("Service configuration rejected (details suppressed)", file=sys.stderr)
        return 78
    os.chdir(root)
    os.execv(args[0], args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
