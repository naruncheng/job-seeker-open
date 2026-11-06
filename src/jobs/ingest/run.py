"""Runs every enabled ingest source and collects their RawJobs.

Each source is isolated: a failure in one (exception, missing creds,
network error) is logged and marked degraded, never allowed to kill the run
or the other sources (CLAUDE.md §5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from jobs.config import Settings, load_companies
from jobs.ingest import adzuna, ashby, greenhouse, jobspy_source, lever
from jobs.ingest.base import FetchClient
from jobs.ingest.schema import RawJob

logger = logging.getLogger(__name__)

ATS_FETCHERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


@dataclass
class IngestResult:
    jobs: list[RawJob] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)
    degraded_sources: list[str] = field(default_factory=list)

    def add(self, source: str, jobs: list[RawJob]) -> None:
        self.jobs.extend(jobs)
        self.source_counts[source] = self.source_counts.get(source, 0) + len(jobs)

    def mark_degraded(self, source: str) -> None:
        if source not in self.degraded_sources:
            self.degraded_sources.append(source)


def run_all(settings: Settings) -> IngestResult:
    enabled = set(settings.get("ingest", "sources", default=[]))
    result = IngestResult()

    if "adzuna" in enabled:
        _run_source(result, "adzuna", lambda: adzuna.fetch(settings))

    if "jobspy" in enabled:
        _run_source(result, "jobspy", lambda: jobspy_source.fetch(settings))

    ats_sources_enabled = enabled & set(ATS_FETCHERS)
    if ats_sources_enabled:
        companies = load_companies()
        with FetchClient() as client:
            for company in companies:
                ats = company.get("ats")
                slug = company.get("slug")
                name = company.get("name", slug)
                if not isinstance(ats, str) or not isinstance(slug, str):
                    continue
                if ats not in ats_sources_enabled or not slug:
                    continue
                fetcher = ATS_FETCHERS[ats]
                _run_source(
                    result,
                    f"{ats}:{slug}",
                    lambda fetcher=fetcher, name=name, slug=slug: fetcher(name, slug, client),
                )

    return result


def _run_source(result: IngestResult, source_label: str, fn: object) -> None:
    logger.info("ingest source %s: starting", source_label)
    try:
        jobs: list[RawJob] = fn()  # type: ignore[operator]
    except Exception:
        logger.exception("ingest source %s: failed, marking degraded", source_label)
        result.mark_degraded(source_label)
        return
    logger.info("ingest source %s: got %d jobs", source_label, len(jobs))
    result.add(source_label, jobs)
