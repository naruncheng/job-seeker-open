"""Dedupe + persist RawJobs.

Dedupe key is sha256(normalize(title) + normalize(company) + normalize(location)),
per CLAUDE.md §5. Only genuinely new jobs are inserted; a job seen again just
bumps `last_seen_at`. Re-scoring an already-seen job happens in the score
stage (not here) by checking whether `score` is still null — this stage's
job is purely "have we stored this exact listing before."
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field

from jobs.config import Settings
from jobs.db import upsert_job
from jobs.ingest.schema import RawJob
from jobs.normalize.location import classify_scope, normalize_text

logger = logging.getLogger(__name__)

JOB_ID_LENGTH = 16


@dataclass
class DedupeResult:
    new_job_ids: list[str] = field(default_factory=list)
    duplicate_count: int = 0

    @property
    def new_count(self) -> int:
        return len(self.new_job_ids)


def compute_job_id(title: str, company: str, location_raw: str | None) -> tuple[str, str]:
    """Return (short_id, full_id) for a job's dedupe key."""
    key = "|".join(
        [normalize_text(title), normalize_text(company), normalize_text(location_raw)]
    )
    full = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return full[:JOB_ID_LENGTH], full


def store_jobs(
    conn: sqlite3.Connection, raw_jobs: list[RawJob], settings: Settings
) -> DedupeResult:
    local_city = settings.get("digest", "local_city", default="Melbourne")
    local_country = settings.get("digest", "local_country", default="Australia")

    result = DedupeResult()
    for job in raw_jobs:
        if not job.title or not job.company or not job.url:
            logger.warning(
                "normalize: skipping job missing title/company/url (source=%s)", job.source
            )
            continue

        job_id, job_id_full = compute_job_id(job.title, job.company, job.location_raw)
        scope = classify_scope(job.location_raw, local_city, local_country)
        location_city = local_city if scope == "local" else None
        location_country = local_country if scope in ("local", "domestic") else None

        is_new = upsert_job(
            conn,
            job_id=job_id,
            job_id_full=job_id_full,
            source=job.source,
            source_id=job.source_id,
            title=job.title,
            company=job.company,
            location_raw=job.location_raw,
            location_city=location_city,
            location_country=location_country,
            remote=job.remote,
            url=job.url,
            description=job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            posted_at=job.posted_at,
            raw_payload=job.model_dump_json(),
        )
        if is_new:
            result.new_job_ids.append(job_id)
        else:
            result.duplicate_count += 1

    return result


def add_manual_job(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    title: str,
    company: str,
    url: str,
    location: str | None,
    description: str | None,
) -> tuple[str, bool]:
    """Register a job the user found themselves. Returns (job_id, is_new)."""
    raw_job = RawJob(
        source="manual",
        title=title,
        company=company,
        location_raw=location,
        url=url,
        description=description,
    )
    result = store_jobs(conn, [raw_job], settings)
    job_id, _ = compute_job_id(title, company, location)
    return job_id, bool(result.new_count)
