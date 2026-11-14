"""Hackathon processes using private environment files outside the repository.

Run through uv: python scripts/run_demo.py api|tunnel|worker|scheduler.
Never starts paid generation unless worker.env explicitly enables it.
"""

import argparse
import os
import shutil
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path.home() / ".config/alter/demo"


def environment(role: str) -> dict[str, str]:
    filename = "api.env" if role == "api" else f"runtime-{role}.env"
    path = PRIVATE / filename
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Private environment file must exist with mode 600")
    values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    # Do not pass parent shell credentials into a different role.
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "SSL_CERT_FILE"}}
    env.update(values)
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["api", "tunnel", "worker", "scheduler"])
    args = parser.parse_args()
    try:
        if (ROOT / ".env").exists():
            raise ValueError("Demo checkout must not contain a root .env")
        binary = ROOT / ".venv/bin"
        if args.role == "tunnel":
            executable = shutil.which("cloudflared")
            if not executable:
                raise ValueError("Install cloudflared first")
            command = [executable, "tunnel", "--url", "http://127.0.0.1:8001"]
            env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG"}}
        else:
            env = environment(args.role)
            if env.get("ALTER_MODE") != "live":
                raise ValueError("Live configuration required")
            if args.role == "api":
                command = [
                    str(binary / "uvicorn"),
                    "alter_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--workers",
                    "1",
                    "--no-access-log",
                    "--no-proxy-headers",
                    "--log-level",
                    "critical",
                ]
            else:
                if args.role == "worker" and env.get("LLM_ENABLED") != "true":
                    raise ValueError("Enable LLM explicitly after consent and budget review")
                entry = "alter-worker" if args.role == "worker" else "alter-scheduler"
                command = [str(binary / entry)]
    except Exception:
        print("Demo startup rejected; inspect private configuration (values suppressed)")
        return 78
    os.chdir(ROOT)
    os.execve(command[0], command, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
