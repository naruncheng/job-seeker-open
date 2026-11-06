from __future__ import annotations

import sqlite3

import pytest

from jobs.config import Secrets, Settings
from jobs.db import get_job, upsert_job
from jobs.score import run as score_run_module
from jobs.score.llm import ScoringUnavailable, Usage
from jobs.score.run import run_scoring
from jobs.score.schema import JobScore

SETTINGS = Settings(
    raw={"scoring": {"batch_size": 2, "budget_cap_usd": 1.00, "threshold": 7}}
)

SECRETS_WITH_KEY = Secrets(
    anthropic_api_key="fake-key",
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

SECRETS_NO_KEY = Secrets(
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


def _insert_job(conn: sqlite3.Connection, job_id: str) -> None:
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


def test_run_scoring_skips_gracefully_without_api_key(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_NO_KEY)
    _insert_job(db_conn, "job1")

    result = run_scoring(db_conn, SETTINGS)

    assert result.scored_count == 0
    assert result.degraded_reason == "missing ANTHROPIC_API_KEY"


def test_run_scoring_writes_scores_back_to_db(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(score_run_module, "anthropic", _FakeAnthropicModule())
    _insert_job(db_conn, "job1")
    _insert_job(db_conn, "job2")

    def fake_score_batch(client: object, jobs: list[sqlite3.Row], profile_text: str) -> tuple:
        scores = [
            JobScore(
                job_id=job["job_id"],
                relevance=9,
                scope="local",
                industry="Robotics",
                role_type="Graduate Program",
                visa_flag=None,
                reason="Great fit.",
                deadline=None,
            )
            for job in jobs
        ]
        return scores, Usage(100, 50, 0, 0)

    monkeypatch.setattr(score_run_module, "score_batch", fake_score_batch)
    monkeypatch.setattr(
        score_run_module, "load_profile_text", lambda: "profile text"
    )

    result = run_scoring(db_conn, SETTINGS)

    assert result.scored_count == 2
    assert result.skipped_count == 0
    assert result.spend_usd > 0
    stored = get_job(db_conn, "job1")
    assert stored is not None
    assert stored["relevance"] == 9
    assert stored["scored_at"] is not None


def test_run_scoring_does_not_rescore_by_default(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(score_run_module, "load_profile_text", lambda: "profile text")
    _insert_job(db_conn, "job1")

    calls: list[int] = []

    def fake_score_batch(client: object, jobs: list[sqlite3.Row], profile_text: str) -> tuple:
        calls.append(len(jobs))
        scores = [
            JobScore(
                job_id=job["job_id"],
                relevance=9,
                scope="local",
                industry="Robotics",
                role_type="Graduate Program",
                visa_flag=None,
                reason="Great fit.",
                deadline=None,
            )
            for job in jobs
        ]
        return scores, Usage(100, 50, 0, 0)

    monkeypatch.setattr(score_run_module, "score_batch", fake_score_batch)

    run_scoring(db_conn, SETTINGS)
    result2 = run_scoring(db_conn, SETTINGS)

    assert calls == [1]  # second run found nothing left to score
    assert result2.jobs_considered == 0


def test_run_scoring_aborts_when_budget_exceeded(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(score_run_module, "load_profile_text", lambda: "profile text")
    for i in range(4):
        _insert_job(db_conn, f"job{i}")

    tiny_budget_settings = Settings(
        raw={"scoring": {"batch_size": 1, "budget_cap_usd": 0.0001}}
    )

    def expensive_score_batch(
        client: object, jobs: list[sqlite3.Row], profile_text: str
    ) -> tuple:
        scores = [
            JobScore(
                job_id=job["job_id"],
                relevance=9,
                scope="local",
                industry="Robotics",
                role_type="Graduate Program",
                visa_flag=None,
                reason="Great fit.",
                deadline=None,
            )
            for job in jobs
        ]
        return scores, Usage(1_000_000, 1_000_000, 0, 0)  # deliberately huge

    monkeypatch.setattr(score_run_module, "score_batch", expensive_score_batch)

    result = run_scoring(db_conn, tiny_budget_settings)

    assert result.aborted_due_to_budget is True
    assert result.scored_count < 4


class _FakeAnthropicModule:
    def Anthropic(self, **kwargs: object) -> object:  # noqa: N802
        return object()


def test_run_scoring_stops_and_reports_when_key_is_rejected(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(score_run_module, "anthropic", _FakeAnthropicModule())
    monkeypatch.setattr(score_run_module, "load_profile_text", lambda: "profile text")
    for i in range(4):
        _insert_job(db_conn, f"job{i}")
    calls: list[int] = []

    def rejecting_score_batch(client: object, jobs: list[sqlite3.Row], profile_text: str) -> tuple:
        calls.append(len(jobs))
        raise ScoringUnavailable("not scoped to a workspace")

    monkeypatch.setattr(score_run_module, "score_batch", rejecting_score_batch)

    result = run_scoring(db_conn, SETTINGS)

    assert calls == [2]  # stopped after the first batch of two
    assert result.scored_count == 0
    assert result.skipped_count == 4
    assert result.degraded_reason == "API key rejected: not scoped to a workspace"


def test_run_scoring_reports_degraded_when_every_batch_fails(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(score_run_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(score_run_module, "anthropic", _FakeAnthropicModule())
    monkeypatch.setattr(score_run_module, "load_profile_text", lambda: "profile text")
    for i in range(3):
        _insert_job(db_conn, f"job{i}")
    monkeypatch.setattr(
        score_run_module, "score_batch", lambda *args: ([], Usage(0, 0, 0, 0))
    )

    result = run_scoring(db_conn, SETTINGS)

    assert result.degraded_reason == "all 3 jobs failed to score (see logs)"
