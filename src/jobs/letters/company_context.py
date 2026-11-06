"""Best-effort fetch of a company's homepage as extra context for a cover
letter. This is optional colour, not something a letter depends on — any
failure (network, blocked, no robots.txt permission) just means the letter
is drafted without it, never a crash (CLAUDE.md §5/§7)."""

from __future__ import annotations

import html
import logging
import re
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from jobs.config import user_agent

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 3000
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_company_context(job_url: str) -> str | None:
    parsed = urlparse(job_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    domain_root = f"{parsed.scheme}://{parsed.netloc}"
    agent = user_agent()

    try:
        with httpx.Client(
            headers={"User-Agent": agent}, timeout=8, follow_redirects=True
        ) as client:
            robots = urllib.robotparser.RobotFileParser()
            robots_resp = client.get(f"{domain_root}/robots.txt")
            if robots_resp.status_code == 200:
                robots.parse(robots_resp.text.splitlines())
            else:
                robots.parse([])
            if not robots.can_fetch(agent, domain_root):
                logger.info("company-context: robots.txt disallows %s, skipping", domain_root)
                return None

            resp = client.get(domain_root)
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("company-context: fetch failed for %s, continuing without it", domain_root)
        return None

    text = _TAG_RE.sub(" ", html.unescape(resp.text))
    text = " ".join(text.split())
    return text[:MAX_CONTEXT_CHARS] or None
