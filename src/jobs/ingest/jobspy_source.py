"""LinkedIn/Indeed/Glassdoor via python-jobspy.

jobspy scrapes sites we don't control and that actively try to block
scrapers, so this wrapper is deliberately paranoid: any exception degrades
the source to zero results rather than killing the run (CLAUDE.md §5), and
we never touch these sites while logged in (CLAUDE.md §7 — jobspy is
read-only/anonymous).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from jobs.config import Settings, user_agent
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)


def fetch(settings: Settings) -> list[RawJob]:
    cfg = settings.get("ingest", "jobspy", default={})
    site_names = cfg.get("site_names", ["indeed"])
    search_term = cfg.get("search_term", "")
    location = cfg.get("location", "")
    results_wanted = int(cfg.get("results_wanted", 30))
    hours_old = cfg.get("hours_old")
    # LinkedIn's search results carry no description; this makes jobspy open each
    # posting (one extra request per LinkedIn job) so scoring/letters see the ad.
    linkedin_fetch_description = bool(cfg.get("linkedin_fetch_description", True))

    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.warning("jobspy: python-jobspy not installed, skipping source")
        return []

    try:
        df = scrape_jobs(
            site_name=site_names,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            country_indeed="Australia",
            linkedin_fetch_description=linkedin_fetch_description,
            user_agent=user_agent(),
        )
    except Exception:
        logger.exception("jobspy: scrape failed, returning 0 jobs")
        return []

    if df is None or df.empty:
        return []

    jobs: list[RawJob] = []
    for _, row in df.iterrows():
        try:
            jobs.append(_row_to_raw_job(row))
        except Exception:
            logger.exception("jobspy: skipping one malformed row")
    return jobs


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _row_to_raw_job(row: pd.Series) -> RawJob:
    site = _clean(row.get("site")) or "jobspy"
    date_posted = _clean(row.get("date_posted"))
    return RawJob(
        source=f"jobspy:{site}",
        source_id=str(_clean(row.get("id"))) if _clean(row.get("id")) is not None else None,
        title=str(_clean(row.get("title")) or "").strip(),
        company=str(_clean(row.get("company")) or "").strip(),
        location_raw=_clean(row.get("location")),
        url=str(_clean(row.get("job_url")) or ""),
        description=_clean(row.get("description")),
        salary_min=_clean(row.get("min_amount")),
        salary_max=_clean(row.get("max_amount")),
        salary_currency=_clean(row.get("currency")),
        posted_at=str(date_posted) if date_posted is not None else None,
        remote=bool(_clean(row.get("is_remote")))
        if _clean(row.get("is_remote")) is not None
        else None,
    )
