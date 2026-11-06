"""Adzuna aggregator API (AU jobs)."""

from __future__ import annotations

import logging
from typing import Any

from jobs.config import Settings, load_secrets
from jobs.ingest.base import FetchClient
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs"


def fetch(settings: Settings, client: FetchClient | None = None) -> list[RawJob]:
    secrets = load_secrets()
    if not secrets.adzuna_app_id or not secrets.adzuna_app_key:
        logger.warning("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set, skipping source")
        return []

    cfg = settings.get("ingest", "adzuna", default={})
    country = cfg.get("country", "au")
    what = cfg.get("what", "")
    where = cfg.get("where", "")
    results_per_page = int(cfg.get("results_per_page", 50))
    max_pages = int(cfg.get("max_pages", 2))

    owns_client = client is None
    # Adzuna's API host serves `Disallow: /` in robots.txt, but this is their
    # documented, key-authenticated API — robots.txt is aimed at crawlers, so
    # the check is skipped here (see DECISIONS.md). Rate limits still apply.
    client = client or FetchClient(respect_robots=False)
    # Adzuna's `what` requires ALL words to match, so a literal "a OR b" query
    # matches nothing. Run each OR-separated term as its own search instead;
    # duplicates across terms are collapsed by normalize/dedupe.
    terms = [t.strip() for t in what.split(" OR ") if t.strip()] or [""]
    jobs: list[RawJob] = []
    try:
        for term in terms:
            for page in range(1, max_pages + 1):
                params: dict[str, Any] = {
                    "app_id": secrets.adzuna_app_id,
                    "app_key": secrets.adzuna_app_key,
                    "results_per_page": results_per_page,
                    "content-type": "application/json",
                }
                if term:
                    params["what"] = term
                if where:
                    params["where"] = where
                data = client.get_json(f"{BASE_URL}/{country}/search/{page}", params=params)
                results = data.get("results", []) if isinstance(data, dict) else []
                if not results:
                    break
                for item in results:
                    jobs.append(_to_raw_job(item))
    except Exception:
        logger.exception("adzuna: fetch failed after %d jobs", len(jobs))
        if not jobs:
            raise  # nothing collected: let the runner mark the source degraded
    finally:
        if owns_client:
            client.close()
    return jobs


def _to_raw_job(item: dict[str, Any]) -> RawJob:
    company = item.get("company", {}) or {}
    location = item.get("location", {}) or {}
    return RawJob(
        source="adzuna",
        source_id=str(item.get("id")) if item.get("id") is not None else None,
        title=item.get("title", "").strip(),
        company=(company.get("display_name") or "").strip(),
        location_raw=location.get("display_name"),
        url=item.get("redirect_url", ""),
        description=item.get("description"),
        salary_min=item.get("salary_min"),
        salary_max=item.get("salary_max"),
        salary_currency="AUD",
        posted_at=item.get("created"),
        remote=None,
    )
