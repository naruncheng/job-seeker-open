"""SQLite storage for ingested jobs.

Plain SQL, no ORM, per CLAUDE.md §4. `migrate()` is idempotent and safe to
call on every run; it's how schema changes ship (add a new
`ALTER TABLE ... ADD COLUMN` guarded by a migrations-table version bump,
never a destructive rewrite).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobs.config import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "jobs.db"

_SCHEMA_VERSION = 1

_CREATE_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_id_full TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location_raw TEXT,
    location_city TEXT,
    location_country TEXT,
    remote INTEGER,
    url TEXT NOT NULL,
    description TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    notified_at TEXT,
    raw_payload TEXT
);
"""

_CREATE_JOBS_SOURCE_IDX = """
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
"""

_ADD_SCORE_COLUMNS = [
    "ALTER TABLE jobs ADD COLUMN relevance INTEGER",
    "ALTER TABLE jobs ADD COLUMN scope TEXT",
    "ALTER TABLE jobs ADD COLUMN industry TEXT",
    "ALTER TABLE jobs ADD COLUMN role_type TEXT",
    "ALTER TABLE jobs ADD COLUMN visa_flag TEXT",
    "ALTER TABLE jobs ADD COLUMN reason TEXT",
    "ALTER TABLE jobs ADD COLUMN deadline TEXT",
    "ALTER TABLE jobs ADD COLUMN scored_at TEXT",
]

_CREATE_JOBS_SCORED_IDX = """
CREATE INDEX IF NOT EXISTS idx_jobs_scored_at ON jobs(scored_at);
"""

_CREATE_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""

_CREATE_RUN_LOG_STAGE_IDX = """
CREATE INDEX IF NOT EXISTS idx_run_log_stage ON run_log(stage, finished_at);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(_CREATE_MIGRATIONS)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        if 1 not in applied:
            conn.execute(_CREATE_JOBS)
            conn.execute(_CREATE_JOBS_SOURCE_IDX)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (1, now_iso()),
            )
        if 2 not in applied:
            for statement in _ADD_SCORE_COLUMNS:
                conn.execute(statement)
            conn.execute(_CREATE_JOBS_SCORED_IDX)
            conn.execute(_CREATE_RUN_LOG)
            conn.execute(_CREATE_RUN_LOG_STAGE_IDX)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (2, now_iso()),
            )


def upsert_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    job_id_full: str,
    source: str,
    source_id: str | None,
    title: str,
    company: str,
    location_raw: str | None,
    location_city: str | None,
    location_country: str | None,
    remote: bool | None,
    url: str,
    description: str | None,
    salary_min: float | None,
    salary_max: float | None,
    salary_currency: str | None,
    posted_at: str | None,
    raw_payload: str | None,
) -> bool:
    """Insert a job, or refresh last_seen_at if it's already known.

    A known job whose stored description is empty gets it filled in from this
    sighting (e.g. LinkedIn descriptions we only started fetching later); scores
    and everything else are left alone.

    Returns True if this was a new job (first time this dedupe key was seen).
    """
    existing = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    timestamp = now_iso()
    if existing:
        with conn:
            conn.execute(
                "UPDATE jobs SET last_seen_at = ?,"
                " description = COALESCE(NULLIF(description, ''), ?) WHERE job_id = ?",
                (timestamp, description, job_id),
            )
        return False

    with conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, job_id_full, source, source_id, title, company,
                location_raw, location_city, location_country, remote, url,
                description, salary_min, salary_max, salary_currency,
                posted_at, first_seen_at, last_seen_at, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job_id_full,
                source,
                source_id,
                title,
                company,
                location_raw,
                location_city,
                location_country,
                None if remote is None else int(remote),
                url,
                description,
                salary_min,
                salary_max,
                salary_currency,
                posted_at,
                timestamp,
                timestamp,
                raw_payload,
            ),
        )
    return True


def get_unnotified_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE notified_at IS NULL ORDER BY first_seen_at ASC"
    ).fetchall()


def mark_notified(conn: sqlite3.Connection, job_ids: Iterable[str]) -> None:
    ids = list(job_ids)
    if not ids:
        return
    timestamp = now_iso()
    with conn:
        conn.executemany(
            "UPDATE jobs SET notified_at = ? WHERE job_id = ?",
            [(timestamp, job_id) for job_id in ids],
        )


def count_jobs(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    return int(row[0])


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    with closing(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))) as cur:
        row = cur.fetchone()
    return dict(row) if row else None


def list_jobs(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    min_relevance: int | None = None,
    source: str | None = None,
    sort: str = "newest",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """Filtered page of jobs for browsing, plus the total match count."""
    where: list[str] = []
    params: list[Any] = []
    if query:
        where.append("(title LIKE ? OR company LIKE ? OR location_raw LIKE ?)")
        like = f"%{query}%"
        params += [like, like, like]
    if min_relevance is not None:
        where.append("relevance >= ?")
        params.append(min_relevance)
    if source:
        where.append("source LIKE ?")
        params.append(f"{source}%")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = {
        "score": "relevance IS NULL, relevance DESC, first_seen_at DESC",
        "company": "company COLLATE NOCASE ASC, first_seen_at DESC",
    }.get(sort, "first_seen_at DESC, relevance DESC")

    total = conn.execute(f"SELECT COUNT(*) FROM jobs {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM jobs {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return rows, int(total)


def update_description(conn: sqlite3.Connection, job_id: str, description: str) -> None:
    with conn:
        conn.execute("UPDATE jobs SET description = ? WHERE job_id = ?", (description, job_id))


def get_jobs_needing_score(conn: sqlite3.Connection, force: bool = False) -> list[sqlite3.Row]:
    """Jobs to score this run. Never re-scores unless `force` (CLAUDE.md §5)."""
    if force:
        return conn.execute("SELECT * FROM jobs ORDER BY first_seen_at ASC").fetchall()
    return conn.execute(
        "SELECT * FROM jobs WHERE scored_at IS NULL ORDER BY first_seen_at ASC"
    ).fetchall()


def update_job_score(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    relevance: int,
    scope: str,
    industry: str,
    role_type: str,
    visa_flag: str | None,
    reason: str,
    deadline: str | None,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE jobs SET
                relevance = ?, scope = ?, industry = ?, role_type = ?,
                visa_flag = ?, reason = ?, deadline = ?, scored_at = ?
            WHERE job_id = ?
            """,
            (relevance, scope, industry, role_type, visa_flag, reason, deadline, now_iso(), job_id),
        )


def get_scored_unnotified_jobs(
    conn: sqlite3.Connection, min_relevance: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM jobs
        WHERE notified_at IS NULL AND scored_at IS NOT NULL AND relevance >= ?
        ORDER BY scope ASC, industry ASC, role_type ASC, relevance DESC
        """,
        (min_relevance,),
    ).fetchall()


def log_run(conn: sqlite3.Connection, stage: str, summary: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT INTO run_log (stage, finished_at, summary_json) VALUES (?, ?, ?)",
            (stage, now_iso(), json.dumps(summary)),
        )


def get_latest_run_time(conn: sqlite3.Connection, stage: str) -> str | None:
    row = conn.execute(
        "SELECT finished_at FROM run_log WHERE stage = ? ORDER BY id DESC LIMIT 1", (stage,)
    ).fetchone()
    return str(row[0]) if row else None


def get_latest_run(conn: sqlite3.Connection, stage: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT summary_json FROM run_log WHERE stage = ? ORDER BY id DESC LIMIT 1",
        (stage,),
    ).fetchone()
    return json.loads(row[0]) if row else None
