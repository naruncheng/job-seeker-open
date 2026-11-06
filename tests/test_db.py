from __future__ import annotations

import sqlite3

from jobs.db import count_jobs, get_job, get_unnotified_jobs, mark_notified, upsert_job


def _insert(conn: sqlite3.Connection, job_id: str, title: str = "Engineer") -> None:
    upsert_job(
        conn,
        job_id=job_id,
        job_id_full=job_id * 4,
        source="greenhouse",
        source_id="1",
        title=title,
        company="Acme",
        location_raw="Melbourne",
        location_city="Melbourne",
        location_country="Australia",
        remote=False,
        url=f"https://example.com/{job_id}",
        description="desc",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        raw_payload="{}",
    )


def test_upsert_job_inserts_then_updates_last_seen(db_conn: sqlite3.Connection) -> None:
    is_new_1 = _insert_and_check(db_conn, "abc123")
    is_new_2 = _insert_and_check(db_conn, "abc123")

    assert is_new_1 is True
    assert is_new_2 is False
    assert count_jobs(db_conn) == 1


def _insert_and_check(conn: sqlite3.Connection, job_id: str) -> bool:
    return upsert_job(
        conn,
        job_id=job_id,
        job_id_full=job_id * 4,
        source="greenhouse",
        source_id="1",
        title="Engineer",
        company="Acme",
        location_raw="Melbourne",
        location_city="Melbourne",
        location_country="Australia",
        remote=False,
        url="https://example.com/1",
        description="desc",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        raw_payload="{}",
    )


def test_get_job_returns_none_for_unknown_id(db_conn: sqlite3.Connection) -> None:
    assert get_job(db_conn, "does-not-exist") is None


def test_unnotified_jobs_and_mark_notified(db_conn: sqlite3.Connection) -> None:
    _insert(db_conn, "job1")
    _insert(db_conn, "job2")

    unnotified = get_unnotified_jobs(db_conn)
    assert {row["job_id"] for row in unnotified} == {"job1", "job2"}

    mark_notified(db_conn, ["job1"])

    unnotified_after = get_unnotified_jobs(db_conn)
    assert {row["job_id"] for row in unnotified_after} == {"job2"}


def test_mark_notified_with_empty_list_is_a_noop(db_conn: sqlite3.Connection) -> None:
    _insert(db_conn, "job1")
    mark_notified(db_conn, [])
    assert len(get_unnotified_jobs(db_conn)) == 1


def _upsert_with_description(conn: sqlite3.Connection, description: str | None) -> bool:
    return upsert_job(
        conn,
        job_id="abc123",
        job_id_full="abc123" * 4,
        source="jobspy:linkedin",
        source_id="1",
        title="Engineer",
        company="Acme",
        location_raw="Melbourne",
        location_city="Melbourne",
        location_country="Australia",
        remote=False,
        url="https://example.com/1",
        description=description,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        raw_payload="{}",
    )


def test_resighting_backfills_a_missing_description(db_conn: sqlite3.Connection) -> None:
    _upsert_with_description(db_conn, None)
    assert _upsert_with_description(db_conn, "Full ad text") is False
    assert get_job(db_conn, "abc123")["description"] == "Full ad text"


def test_resighting_never_overwrites_an_existing_description(db_conn: sqlite3.Connection) -> None:
    _upsert_with_description(db_conn, "Original ad")
    _upsert_with_description(db_conn, "Different text")
    assert get_job(db_conn, "abc123")["description"] == "Original ad"
