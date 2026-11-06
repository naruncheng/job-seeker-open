from __future__ import annotations

import sqlite3

import pytest

from jobs.config import Secrets, Settings
from jobs.db import get_unnotified_jobs, log_run, update_job_score, upsert_job
from jobs.digest import service as service_module
from jobs.digest.service import run_digest

SETTINGS = Settings(raw={"scoring": {"threshold": 7}})

SECRETS_ALL_CONFIGURED = Secrets(
    anthropic_api_key=None,
    adzuna_app_id=None,
    adzuna_app_key=None,
    discord_webhook_url="https://discord.example/webhook",
    smtp_host="smtp.example.com",
    smtp_port=587,
    smtp_user="user",
    smtp_pass="pass",
    digest_to_email="me@example.com",
    digest_from_email="digest@example.com",
    user_agent_email=None,
)

SECRETS_NONE_CONFIGURED = Secrets(
    anthropic_api_key=None,
    adzuna_app_id=None,
    adzuna_app_key=None,
    discord_webhook_url=None,
    smtp_host=None,
    smtp_port=587,
    smtp_user=None,
    smtp_pass=None,
    digest_to_email=None,
    digest_from_email=None,
    user_agent_email=None,
)


def _insert_scored_job(conn: sqlite3.Connection, job_id: str, relevance: int) -> None:
    upsert_job(
        conn,
        job_id=job_id,
        job_id_full=job_id * 4,
        source="greenhouse",
        source_id="1",
        title=f"Engineer {job_id}",
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
    update_job_score(
        conn,
        job_id=job_id,
        relevance=relevance,
        scope="local",
        industry="Robotics",
        role_type="Graduate Program",
        visa_flag=None,
        reason="Good fit",
        deadline=None,
    )


def test_run_digest_marks_notified_when_both_channels_send(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", lambda *a, **k: True)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)
    _insert_scored_job(db_conn, "job1", relevance=9)

    result = run_digest(db_conn, SETTINGS)

    assert result.job_count == 1
    assert result.discord_sent is True
    assert result.email_sent is True
    assert get_unnotified_jobs(db_conn) == []


def test_run_digest_excludes_jobs_below_threshold(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", lambda *a, **k: True)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)
    _insert_scored_job(db_conn, "low", relevance=3)

    result = run_digest(db_conn, SETTINGS)

    assert result.job_count == 0
    # Job stays unnotified since it never met the threshold to be included.
    assert len(get_unnotified_jobs(db_conn)) == 1


def test_run_digest_leaves_jobs_unnotified_if_nothing_sends(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_NONE_CONFIGURED)
    _insert_scored_job(db_conn, "job1", relevance=9)

    result = run_digest(db_conn, SETTINGS)

    assert result.discord_sent is False
    assert result.email_sent is False
    assert len(get_unnotified_jobs(db_conn)) == 1


def test_run_digest_marks_notified_if_only_one_channel_sends(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", lambda *a, **k: False)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)
    _insert_scored_job(db_conn, "job1", relevance=9)

    result = run_digest(db_conn, SETTINGS)

    assert result.any_sent is True
    assert get_unnotified_jobs(db_conn) == []


def test_run_digest_reads_footer_data_from_latest_ingest_run_log(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_discord(webhook_url, grouped, source_counts, degraded_sources, **kwargs):  # noqa: ANN001
        captured["source_counts"] = source_counts
        captured["degraded_sources"] = degraded_sources
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)

    log_run(
        db_conn,
        "ingest",
        {"source_counts": {"greenhouse:acme": 5}, "degraded_sources": ["jobspy"]},
    )
    _insert_scored_job(db_conn, "job1", relevance=9)

    run_digest(db_conn, SETTINGS)

    assert captured["source_counts"] == {"greenhouse:acme": 5}
    assert captured["degraded_sources"] == ["jobspy"]


def test_run_digest_passes_the_latest_scoring_cost_to_both_channels(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_discord(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["discord"] = kwargs.get("scoring_cost")
        return True

    def fake_email(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["email"] = kwargs.get("scoring_cost")
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", fake_email)
    log_run(db_conn, "score", {"spend_usd": 0.5})  # an older run
    log_run(db_conn, "score", {"spend_usd": 0.0289})  # tonight's
    _insert_scored_job(db_conn, "job1", relevance=9)

    run_digest(db_conn, SETTINGS)

    assert captured == {"discord": 0.0289, "email": 0.0289}


def test_run_digest_passes_the_ingest_new_count_to_both_channels(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_discord(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["discord"] = kwargs.get("new_count")
        return True

    def fake_email(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["email"] = kwargs.get("new_count")
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", fake_email)
    log_run(db_conn, "ingest", {"new_count": 99})  # an older run
    log_run(db_conn, "ingest", {"new_count": 12})  # tonight's
    _insert_scored_job(db_conn, "job1", relevance=9)

    run_digest(db_conn, SETTINGS)

    assert captured == {"discord": 12, "email": 12}


def test_run_digest_passes_no_new_count_when_ingest_never_ran(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None, not 0 — the footer must not claim nothing new was found when the
    truth is that no ingest run was logged to read a count from."""
    captured: dict = {}

    def fake_discord(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["discord"] = kwargs.get("new_count", "missing")
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)
    _insert_scored_job(db_conn, "job1", relevance=9)

    run_digest(db_conn, SETTINGS)

    assert captured["discord"] is None


def test_run_digest_passes_no_cost_when_scoring_never_ran(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_discord(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["discord"] = kwargs.get("scoring_cost", "missing")
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", lambda *a, **k: True)
    _insert_scored_job(db_conn, "job1", relevance=9)

    run_digest(db_conn, SETTINGS)

    assert captured["discord"] is None


def test_run_digest_flags_a_failed_scoring_run_as_degraded(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken scorer means zero jobs above threshold; that must not read as
    an ordinary "nothing new today"."""
    captured: dict = {}

    def fake_discord(webhook_url, grouped, source_counts, degraded_sources, **kwargs):  # noqa: ANN001, ANN003
        captured["discord"] = degraded_sources
        return True

    def fake_email(secrets, grouped, total, source_counts, degraded_sources, **kwargs):  # noqa: ANN001, ANN003
        captured["email"] = degraded_sources
        return True

    monkeypatch.setattr(service_module, "load_secrets", lambda: SECRETS_ALL_CONFIGURED)
    monkeypatch.setattr(service_module, "send_discord_digest", fake_discord)
    monkeypatch.setattr(service_module, "send_email_digest", fake_email)
    log_run(db_conn, "ingest", {"degraded_sources": ["jobspy"]})
    log_run(db_conn, "score", {"spend_usd": 0.0, "degraded_reason": "API key rejected: 400"})

    run_digest(db_conn, SETTINGS)

    expected = ["jobspy", "scoring (API key rejected: 400)"]
    assert captured == {"discord": expected, "email": expected}
