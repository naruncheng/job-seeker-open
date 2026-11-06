"""Ties grouping + Discord + email together into the nightly digest.

Both channels run every run (CLAUDE.md §5); each degrades independently if
unconfigured. Jobs are marked notified only if at least one channel actually
sent, so a total outage leaves them to retry next run instead of being
silently dropped.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from jobs.config import Settings, load_secrets
from jobs.db import get_latest_run, get_scored_unnotified_jobs, mark_notified
from jobs.digest.dates import DEFAULT_TIMEZONE, run_date_label
from jobs.digest.discord import send_discord_digest
from jobs.digest.email import send_email_digest
from jobs.digest.grouping import group_jobs

logger = logging.getLogger(__name__)


@dataclass
class DigestResult:
    job_count: int = 0
    discord_sent: bool = False
    email_sent: bool = False

    @property
    def any_sent(self) -> bool:
        return self.discord_sent or self.email_sent


def run_digest(conn: sqlite3.Connection, settings: Settings) -> DigestResult:
    threshold = int(settings.get("scoring", "threshold", default=7))
    rows = get_scored_unnotified_jobs(conn, threshold)
    jobs = [dict(row) for row in rows]
    grouped = group_jobs(jobs)

    ingest_summary = get_latest_run(conn, "ingest") or {}
    source_counts: dict[str, int] = ingest_summary.get("source_counts", {})
    degraded_sources: list[str] = list(ingest_summary.get("degraded_sources", []))
    # None (not 0) when no ingest run has been logged, so the footer can omit
    # the count rather than claiming nothing new was found.
    new_count: int | None = ingest_summary.get("new_count")
    # Spend of the most recent scoring run (tonight's, when run by the nightly job).
    score_summary = get_latest_run(conn, "score") or {}
    scoring_cost: float | None = score_summary.get("spend_usd")
    # A broken scorer yields zero jobs above threshold, which would otherwise
    # read as "nothing new today" — flag it alongside the degraded sources.
    if score_reason := score_summary.get("degraded_reason"):
        degraded_sources.append(f"scoring ({score_reason})")

    secrets = load_secrets()
    result = DigestResult(job_count=len(jobs))
    run_date = run_date_label(tz_name=settings.get("digest", "timezone", default=DEFAULT_TIMEZONE))

    if secrets.discord_webhook_url:
        result.discord_sent = send_discord_digest(
            secrets.discord_webhook_url,
            grouped,
            source_counts,
            degraded_sources,
            run_date=run_date,
            scoring_cost=scoring_cost,
            new_count=new_count,
        )
    else:
        logger.warning("discord digest: skipping, DISCORD_WEBHOOK_URL not set")

    result.email_sent = send_email_digest(
        secrets,
        grouped,
        len(jobs),
        source_counts,
        degraded_sources,
        run_date=run_date,
        scoring_cost=scoring_cost,
        new_count=new_count,
    )

    if result.any_sent:
        mark_notified(conn, [job["job_id"] for job in jobs])
    elif jobs:
        logger.warning(
            "digest: no channel sent successfully, leaving %d jobs unnotified for retry",
            len(jobs),
        )

    return result
