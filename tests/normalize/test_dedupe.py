from __future__ import annotations

import sqlite3

from jobs.config import Settings
from jobs.db import get_job
from jobs.ingest.schema import RawJob
from jobs.normalize.dedupe import compute_job_id, store_jobs

SETTINGS = Settings(raw={"digest": {"local_city": "Melbourne", "local_country": "Australia"}})


def _job(**overrides: object) -> RawJob:
    defaults = dict(
        source="greenhouse",
        source_id="1",
        title="Graduate Mechatronics Engineer",
        company="Acme Robotics",
        location_raw="Melbourne VIC",
        url="https://example.com/jobs/1",
        description="desc",
    )
    defaults.update(overrides)
    return RawJob(**defaults)  # type: ignore[arg-type]


def test_compute_job_id_is_stable_and_case_insensitive() -> None:
    id_a, full_a = compute_job_id("Graduate Engineer", "Acme", "Melbourne VIC")
    id_b, full_b = compute_job_id("  graduate   engineer ", "ACME", "melbourne vic")
    assert id_a == id_b
    assert full_a == full_b
    assert len(id_a) == 16
    assert full_a.startswith(id_a)


def test_compute_job_id_differs_for_different_jobs() -> None:
    id_a, _ = compute_job_id("Engineer", "Acme", "Melbourne")
    id_b, _ = compute_job_id("Engineer", "Beta", "Melbourne")
    assert id_a != id_b


def test_store_jobs_inserts_new_and_dedupes_repeats(db_conn: sqlite3.Connection) -> None:
    jobs = [_job(), _job()]  # identical -> same dedupe key

    result = store_jobs(db_conn, jobs, SETTINGS)

    assert result.new_count == 1
    assert result.duplicate_count == 1

    stored = get_job(db_conn, result.new_job_ids[0])
    assert stored is not None
    assert stored["title"] == "Graduate Mechatronics Engineer"
    assert stored["location_city"] == "Melbourne"


def test_store_jobs_rerun_is_all_duplicates(db_conn: sqlite3.Connection) -> None:
    jobs = [_job()]
    first = store_jobs(db_conn, jobs, SETTINGS)
    second = store_jobs(db_conn, jobs, SETTINGS)

    assert first.new_count == 1
    assert second.new_count == 0
    assert second.duplicate_count == 1


def test_store_jobs_skips_incomplete_rows(db_conn: sqlite3.Connection) -> None:
    jobs = [_job(title=""), _job(company=""), _job(url="")]

    result = store_jobs(db_conn, jobs, SETTINGS)

    assert result.new_count == 0
    assert result.duplicate_count == 0
