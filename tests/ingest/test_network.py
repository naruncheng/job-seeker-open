"""Integration tests against real, live endpoints.

Skipped by default — run with `pytest --run-network`. One per ingest source,
per CLAUDE.md §6. These use no-auth-required public boards where possible so
they run without secrets; adzuna verifies graceful degradation on auth
failure since we don't ship real Adzuna credentials in this repo.
"""

from __future__ import annotations

import pytest

from jobs.config import Secrets, Settings
from jobs.ingest import adzuna, ashby, greenhouse, jobspy_source, lever

pytestmark = pytest.mark.network


def test_greenhouse_fetch_real_board() -> None:
    jobs = greenhouse.fetch("GitLab", "gitlab")
    assert len(jobs) > 0
    assert all(job.url.startswith("http") for job in jobs)


def test_lever_fetch_real_board() -> None:
    jobs = lever.fetch("Lever (demo board)", "leverdemo")
    assert len(jobs) > 0
    assert all(job.title for job in jobs)


def test_ashby_fetch_real_board() -> None:
    jobs = ashby.fetch("Ramp", "ramp")
    assert len(jobs) > 0
    assert all(job.title for job in jobs)


def test_adzuna_fetch_degrades_gracefully_on_real_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # We don't ship real Adzuna credentials in this repo, so this test hits
    # the real endpoint with throwaway ones and confirms the resulting 401
    # is caught (per CLAUDE.md §5, one dead source never kills the run)
    # rather than propagating as a crash.
    fake_secrets = Secrets(
        anthropic_api_key=None,
        adzuna_app_id="throwaway-id",
        adzuna_app_key="throwaway-key",
        discord_webhook_url=None,
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_pass=None,
        digest_to_email=None,
        digest_from_email=None,
        user_agent_email=None,
    )
    monkeypatch.setattr(adzuna, "load_secrets", lambda: fake_secrets)
    settings = Settings(raw={"ingest": {"adzuna": {"max_pages": 1}}})

    jobs = adzuna.fetch(settings)

    assert jobs == []


def test_jobspy_fetch_does_not_raise() -> None:
    settings = Settings(
        raw={
            "ingest": {
                "jobspy": {
                    "site_names": ["indeed"],
                    "search_term": "mechatronics engineer",
                    "location": "Melbourne, Australia",
                    "results_wanted": 5,
                    "hours_old": 72,
                }
            }
        }
    )
    jobs = jobspy_source.fetch(settings)
    assert isinstance(jobs, list)
