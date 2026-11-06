"""Runtime configuration: .env secrets + config/*.yaml settings.

Nothing here is cached at import time beyond what's needed to avoid re-reading
files on every access within a single run — call `load_settings()` /
`load_companies()` fresh per pipeline run so edits to the YAML take effect
without a restart.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
OUTPUTS_DIR = REPO_ROOT / "outputs"
INPUTS_DIR = REPO_ROOT / "inputs"

# Resume formats accepted, in order of preference when several exist.
RESUME_SUFFIXES = (".pdf", ".docx", ".txt", ".md")


def find_resume(preferred: Path | None = None) -> Path | None:
    """Locate the resume: `preferred` if it exists, else the same name in any
    supported format (resume.pdf, resume.docx, resume.txt, resume.md)."""
    base = preferred or INPUTS_DIR / "resume.pdf"
    if base.exists():
        return base
    for suffix in RESUME_SUFFIXES:
        candidate = base.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None

_dotenv_loaded = False


def _ensure_dotenv() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv(REPO_ROOT / ".env")
        _dotenv_loaded = True


@dataclass(frozen=True)
class Secrets:
    anthropic_api_key: str | None
    adzuna_app_id: str | None
    adzuna_app_key: str | None
    discord_webhook_url: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_pass: str | None
    digest_to_email: str | None
    digest_from_email: str | None
    user_agent_email: str | None

    def describe_missing(self, names: list[str]) -> list[str]:
        """Return which of the given secret attribute names are unset."""
        return [n for n in names if not getattr(self, n)]


DEFAULT_SMTP_PORT = 587


def _smtp_port() -> int:
    """SMTP_PORT, defaulting to 587 when unset, blank or not a number.

    GitHub Actions passes a secret you haven't created as an empty string rather
    than leaving the variable unset, so a plain os.getenv default isn't enough.
    """
    raw = (os.getenv("SMTP_PORT") or "").strip()
    if not raw:
        return DEFAULT_SMTP_PORT
    try:
        return int(raw)
    except ValueError:
        logger.warning("SMTP_PORT=%r is not a number, using %d", raw, DEFAULT_SMTP_PORT)
        return DEFAULT_SMTP_PORT


def load_secrets() -> Secrets:
    _ensure_dotenv()
    return Secrets(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        adzuna_app_id=os.getenv("ADZUNA_APP_ID") or None,
        adzuna_app_key=os.getenv("ADZUNA_APP_KEY") or None,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=_smtp_port(),
        smtp_user=os.getenv("SMTP_USER") or None,
        smtp_pass=os.getenv("SMTP_PASS") or None,
        digest_to_email=os.getenv("DIGEST_TO_EMAIL") or None,
        digest_from_email=os.getenv("DIGEST_FROM_EMAIL") or None,
        user_agent_email=os.getenv("USER_AGENT_EMAIL") or None,
    )


def user_agent() -> str:
    """A descriptive User-Agent carrying a contact email, per CLAUDE.md §7."""
    secrets = load_secrets()
    contact = secrets.user_agent_email or "unknown@example.com"
    return f"job-seeker-pipeline/0.1 (+mailto:{contact})"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def load_settings() -> Settings:
    path = CONFIG_DIR / "settings.yaml"
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings(raw=raw)


def load_companies() -> list[dict[str, Any]]:
    path = CONFIG_DIR / "companies.yaml"
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    companies = raw.get("companies", [])
    if not isinstance(companies, list):
        raise ValueError(f"{path}: 'companies' must be a list")
    return companies


def load_profile_text() -> str:
    path = CONFIG_DIR / "profile.md"
    return path.read_text(encoding="utf-8")
