"""Lever job boards: public, no-auth JSON API.

https://api.lever.co/v0/postings/{slug}?mode=json
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from jobs.ingest.base import FetchClient
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"


def fetch(company_name: str, slug: str, client: FetchClient | None = None) -> list[RawJob]:
    owns_client = client is None
    client = client or FetchClient()
    try:
        data = client.get_json(f"{BASE_URL}/{slug}", params={"mode": "json"})
    except Exception:
        logger.exception("lever: fetch failed for board %r", slug)
        return []
    finally:
        if owns_client:
            client.close()

    if not isinstance(data, list):
        return []
    return [_to_raw_job(company_name, item) for item in data]


def _epoch_ms_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


def _to_raw_job(company_name: str, item: dict[str, Any]) -> RawJob:
    categories = item.get("categories") or {}
    return RawJob(
        source="lever",
        source_id=item.get("id"),
        title=(item.get("text") or "").strip(),
        company=company_name,
        location_raw=categories.get("location"),
        url=item.get("hostedUrl", ""),
        description=item.get("descriptionPlain") or item.get("description"),
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_epoch_ms_to_iso(item.get("createdAt")),
        remote=(item.get("workplaceType") == "remote") if item.get("workplaceType") else None,
    )
