"""Stage compositions shared by the CLI and the web UI."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from jobs.config import Settings
from jobs.db import log_run
from jobs.ingest.run import IngestResult, run_all
from jobs.logging_utils import stage
from jobs.normalize.dedupe import DedupeResult, store_jobs
from jobs.score.run import ScoreRunResult, run_scoring

logger = logging.getLogger(__name__)


@dataclass
class IngestRun:
    ingest: IngestResult
    dedupe: DedupeResult


def ingest_and_store(conn: sqlite3.Connection, settings: Settings) -> IngestRun:
    with stage(logger, "ingest") as result:
        ingest_result = run_all(settings)
        result["raw_jobs"] = len(ingest_result.jobs)
        result["degraded_sources"] = len(ingest_result.degraded_sources)

    with stage(logger, "normalize+dedupe", raw_jobs=len(ingest_result.jobs)) as result:
        dedupe_result = store_jobs(conn, ingest_result.jobs, settings)
        result["new"] = dedupe_result.new_count
        result["duplicates"] = dedupe_result.duplicate_count

    log_run(
        conn,
        "ingest",
        {
            "source_counts": ingest_result.source_counts,
            "degraded_sources": ingest_result.degraded_sources,
            "new_count": dedupe_result.new_count,
            "duplicate_count": dedupe_result.duplicate_count,
        },
    )
    return IngestRun(ingest=ingest_result, dedupe=dedupe_result)


def score_and_log(
    conn: sqlite3.Connection, settings: Settings, force: bool = False
) -> ScoreRunResult:
    with stage(logger, "score", jobs_considered=0) as result:
        score_result = run_scoring(conn, settings, force=force)
        result["scored"] = score_result.scored_count
        result["skipped"] = score_result.skipped_count

    log_run(
        conn,
        "score",
        {
            "scored_count": score_result.scored_count,
            "skipped_count": score_result.skipped_count,
            "spend_usd": score_result.spend_usd,
            "aborted_due_to_budget": score_result.aborted_due_to_budget,
            "degraded_reason": score_result.degraded_reason,
        },
    )
    return score_result
