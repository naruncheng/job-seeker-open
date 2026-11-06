"""Greenhouse job boards: public, no-auth JSON API.

https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from jobs.ingest.base import FetchClient
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
_TAG_RE = re.compile(r"<[^>]+>")


def fetch(company_name: str, slug: str, client: FetchClient | None = None) -> list[RawJob]:
    owns_client = client is None
    client = client or FetchClient()
    try:
        data = client.get_json(f"{BASE_URL}/{slug}/jobs", params={"content": "true"})
    except Exception:
        logger.exception("greenhouse: fetch failed for board %r", slug)
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
    location = item.get("location") or {}
    return RawJob(
        source="greenhouse",
        source_id=str(item.get("id")) if item.get("id") is not None else None,
        title=(item.get("title") or "").strip(),
        company=item.get("company_name") or company_name,
        location_raw=location.get("name"),
        url=item.get("absolute_url", ""),
        description=_strip_html(item.get("content")),
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=item.get("first_published") or item.get("updated_at"),
        remote=None,
    )
