"""Ashby job boards: public, no-auth JSON API.

https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from jobs.ingest.base import FetchClient
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"
_TAG_RE = re.compile(r"<[^>]+>")


def fetch(company_name: str, slug: str, client: FetchClient | None = None) -> list[RawJob]:
    owns_client = client is None
    client = client or FetchClient()
    try:
        data = client.get_json(f"{BASE_URL}/{slug}")
    except Exception:
        logger.exception("ashby: fetch failed for board %r", slug)
        return []
    finally:
        if owns_client:
            client.close()

    if not isinstance(data, dict):
        return []
    return [_to_raw_job(company_name, item) for item in data.get("jobs", [])]


def _strip_html(content: str | None) -> str | None:
    if not content:
        return None
    return _TAG_RE.sub(" ", html.unescape(content)).strip()


def _to_raw_job(company_name: str, item: dict[str, Any]) -> RawJob:
    compensation = item.get("compensation") or {}
    return RawJob(
        source="ashby",
        source_id=item.get("id"),
        title=(item.get("title") or "").strip(),
        company=company_name,
        location_raw=item.get("location"),
        url=item.get("jobUrl", ""),
        description=_strip_html(item.get("descriptionHtml")),
        salary_min=compensation.get("min"),
        salary_max=compensation.get("max"),
        salary_currency=compensation.get("currency"),
        posted_at=item.get("publishedAt"),
        remote=item.get("isRemote"),
    )
