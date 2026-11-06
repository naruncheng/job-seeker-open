from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from jobs.db import upsert_job
from jobs.score.llm import ScoringUnavailable, score_batch
from jobs.score.schema import BatchScoreResult, JobScore


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


def _rows(conn: sqlite3.Connection, job_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in job_ids)
    return conn.execute(
        f"SELECT * FROM jobs WHERE job_id IN ({placeholders})", job_ids
    ).fetchall()


def _score(job_id: str, relevance: int = 8) -> JobScore:
    return JobScore(
        job_id=job_id,
        relevance=relevance,
        scope="local",
        industry="Robotics",
        role_type="Graduate Program",
        visa_flag=None,
        reason="Good match.",
        deadline=None,
    )


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def parse(self, **kwargs: Any) -> Any:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def _ok_response(job_ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=500,
            output_tokens=200,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=0,
        ),
        parsed_output=BatchScoreResult(scores=[_score(jid) for jid in job_ids]),
    )


def test_score_batch_succeeds_on_first_attempt(db_conn: sqlite3.Connection) -> None:
    _insert_job(db_conn, "job1")
    _insert_job(db_conn, "job2")
    jobs = _rows(db_conn, ["job1", "job2"])
    client = FakeClient([_ok_response(["job1", "job2"])])

    scores, usage = score_batch(client, jobs, "profile text")  # type: ignore[arg-type]

    assert {s.job_id for s in scores} == {"job1", "job2"}
    assert usage.input_tokens == 500
    assert client.messages.calls == 1


def test_score_batch_retries_once_on_api_error_then_succeeds(
    db_conn: sqlite3.Connection,
) -> None:
    _insert_job(db_conn, "job1")
    jobs = _rows(db_conn, ["job1"])
    error = anthropic.APIConnectionError(request=object())  # type: ignore[arg-type]
    client = FakeClient([error, _ok_response(["job1"])])

    scores, _usage = score_batch(client, jobs, "profile text")  # type: ignore[arg-type]

    assert {s.job_id for s in scores} == {"job1"}
    assert client.messages.calls == 2


def test_score_batch_gives_up_after_second_failure(db_conn: sqlite3.Connection) -> None:
    _insert_job(db_conn, "job1")
    jobs = _rows(db_conn, ["job1"])
    error = anthropic.APIConnectionError(request=object())  # type: ignore[arg-type]
    client = FakeClient([error, error])

    scores, _usage = score_batch(client, jobs, "profile text")  # type: ignore[arg-type]

    assert scores == []
    assert client.messages.calls == 2


def test_score_batch_retries_on_mismatched_job_ids(db_conn: sqlite3.Connection) -> None:
    _insert_job(db_conn, "job1")
    _insert_job(db_conn, "job2")
    jobs = _rows(db_conn, ["job1", "job2"])
    wrong = _ok_response(["job1"])  # missing job2
    right = _ok_response(["job1", "job2"])
    client = FakeClient([wrong, right])

    scores, _usage = score_batch(client, jobs, "profile text")  # type: ignore[arg-type]

    assert {s.job_id for s in scores} == {"job1", "job2"}
    assert client.messages.calls == 2


def test_score_batch_handles_none_parsed_output(db_conn: sqlite3.Connection) -> None:
    _insert_job(db_conn, "job1")
    jobs = _rows(db_conn, ["job1"])
    none_response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=5), parsed_output=None
    )
    client = FakeClient([none_response, none_response])

    scores, _usage = score_batch(client, jobs, "profile text")  # type: ignore[arg-type]

    assert scores == []


@pytest.mark.parametrize("relevance", [0, 10])
def test_job_score_accepts_boundary_relevance(relevance: int) -> None:
    score = _score("job1", relevance=relevance)
    assert score.relevance == relevance


def _status_error(cls: type[anthropic.APIStatusError], status: int, message: str) -> Exception:
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        _status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"),
        _status_error(
            anthropic.BadRequestError,
            400,
            "This API key is not scoped to a workspace, so this request must include "
            "the anthropic-workspace-id header",
        ),
    ],
)
def test_score_batch_raises_without_retry_when_key_is_rejected(
    db_conn: sqlite3.Connection, error: Exception
) -> None:
    _insert_job(db_conn, "job1")
    client = FakeClient([error, _ok_response(["job1"])])

    with pytest.raises(ScoringUnavailable):
        score_batch(client, _rows(db_conn, ["job1"]), "profile text")  # type: ignore[arg-type]

    assert client.messages.calls == 1


def test_score_batch_still_retries_other_bad_requests(db_conn: sqlite3.Connection) -> None:
    _insert_job(db_conn, "job1")
    error = _status_error(anthropic.BadRequestError, 400, "prompt is too long")
    client = FakeClient([error, _ok_response(["job1"])])

    scores, _usage = score_batch(client, _rows(db_conn, ["job1"]), "profile text")  # type: ignore[arg-type]

    assert {s.job_id for s in scores} == {"job1"}
