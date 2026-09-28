"""
Settings from review-intelligence/.env (local) or the environment (GitHub Actions Secrets).
Variables already set in the environment always win over .env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env()


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


LLM_PROVIDER = get("LLM_PROVIDER", "gemini").lower()
LLM_MODEL = get("LLM_MODEL")
LLM_API_KEY = get("LLM_API_KEY")

ALERT_RECIPIENTS = [a.strip() for a in get("ALERT_RECIPIENTS").split(",") if a.strip()]
SMTP_HOST = get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get("SMTP_PORT", "587"))
SMTP_USER = get("SMTP_USER")
SMTP_PASSWORD = get("SMTP_PASSWORD")
ALERT_FROM = get("ALERT_FROM") or SMTP_USER
DASHBOARD_URL = get("DASHBOARD_URL")


def require_llm() -> None:
    """Exit with a plain message (no stack trace) when the LLM key is missing."""
    if not LLM_API_KEY:
        print("LLM_API_KEY is missing. Add a line LLM_API_KEY=<your key> to review-intelligence/.env "
              "(or set it as a GitHub Secret for the scheduled run).", file=sys.stderr)
        sys.exit(2)


def email_configured() -> bool:
    return bool(ALERT_RECIPIENTS and SMTP_USER and SMTP_PASSWORD)
