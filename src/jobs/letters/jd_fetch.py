"""Best-effort fetch + extraction of a job ad from a URL, for `jobs add` when
the user doesn't want to paste the ad by hand. Fetches via `FetchClient`
(rate limit + robots.txt + cache, CLAUDE.md §7), strips markup, then asks
Haiku to pull out just the posting text from the surrounding page chrome.
Any failure degrades to None — the caller falls back to asking for
--description/--jd-file, never a crash."""

from __future__ import annotations

import html
import logging
import re

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from jobs.config import load_secrets
from jobs.ingest.base import FetchClient, RobotsDisallowed, SourceBudgetExceeded
from jobs.score.budget import estimate_cost_usd

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 2048
MAX_PAGE_CHARS = 8000
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)

SYSTEM_INSTRUCTIONS = """\
You are given the scraped text of a web page that may be a job ad. Extract \
only the job posting itself: role summary, responsibilities, requirements, \
salary/location/deadline if stated. Drop navigation menus, cookie banners, \
related-job listings, footers, and any text unrelated to this specific job.

If the page doesn't contain a real job posting (blocked, wrong page, login \
wall, etc.), return an empty string."""


class JobDescriptionExtraction(BaseModel):
    description: str


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", html.unescape(raw))
    return " ".join(text.split())


def fetch_job_description(url: str) -> tuple[str | None, float]:
    """Returns (description or None, spend_usd)."""
    with FetchClient() as fetch_client:
        try:
            page_text = _strip_html(fetch_client.get_text(url))
        except (httpx.HTTPError, RobotsDisallowed, SourceBudgetExceeded) as exc:
            logger.warning("jd-fetch: could not fetch %s: %s", url, exc)
            return None, 0.0

    if not page_text.strip():
        logger.warning("jd-fetch: %s had no extractable text", url)
        return None, 0.0

    secrets = load_secrets()
    if not secrets.anthropic_api_key:
        logger.warning("jd-fetch: ANTHROPIC_API_KEY not set, skipping extraction")
        return None, 0.0
    client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)

    spend = 0.0
    for attempt in (1, 2):
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_INSTRUCTIONS,
                messages=[{"role": "user", "content": page_text[:MAX_PAGE_CHARS]}],
                output_format=JobDescriptionExtraction,
            )
        except (anthropic.APIError, ValidationError) as exc:
            logger.warning("jd-fetch: extraction attempt %d failed: %s", attempt, exc)
            continue

        spend += estimate_cost_usd(MODEL, response.usage.input_tokens, response.usage.output_tokens)
        result = response.parsed_output
        if result is None:
            logger.warning("jd-fetch: extraction attempt %d returned no parsed output", attempt)
            continue
        if not result.description.strip():
            logger.warning("jd-fetch: %s didn't look like a job posting", url)
            return None, spend
        return result.description, spend

    logger.error("jd-fetch: extraction failed after retry for %s", url)
    return None, spend
