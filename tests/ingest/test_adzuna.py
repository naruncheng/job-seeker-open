from __future__ import annotations

import pytest

from jobs.config import Secrets, Settings
from jobs.ingest import adzuna
from tests.conftest import load_fixture
from tests.ingest.conftest import FakeFetchClient

FAKE_SECRETS = Secrets(
    anthropic_api_key=None,
    adzuna_app_id="test-id",
    adzuna_app_key="test-key",
    discord_webhook_url=None,
    smtp_host=None,
    smtp_port=587,
    smtp_user=None,
    smtp_pass=None,
    digest_to_email=None,
    digest_from_email=None,
    user_agent_email=None,
)

MISSING_SECRETS = Secrets(
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

SETTINGS = Settings(
    raw={
        "ingest": {
            "adzuna": {
                "country": "au",
                "what": "engineer",
                "where": "Melbourne",
                "results_per_page": 50,
                "max_pages": 1,
            }
        }
    }
)


def test_fetch_parses_fixture_into_raw_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adzuna, "load_secrets", lambda: FAKE_SECRETS)
    fixture = load_fixture("adzuna_au_search.json")
    client = FakeFetchClient(fixture)

    jobs = adzuna.fetch(SETTINGS, client=client)  # type: ignore[arg-type]

    assert len(jobs) == len(fixture["results"])
    first = jobs[0]
    raw = fixture["results"][0]
    assert first.source == "adzuna"
    assert first.source_id == raw["id"]
    assert first.title == raw["title"]
    assert first.company == raw["company"]["display_name"]
    assert first.location_raw == raw["location"]["display_name"]
    assert first.url == raw["redirect_url"]
    assert first.salary_min == raw["salary_min"]


def test_fetch_stops_paging_on_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adzuna, "load_secrets", lambda: FAKE_SECRETS)
    settings = Settings(raw={"ingest": {"adzuna": {"max_pages": 5}}})
    client = FakeFetchClient({"results": []})

    jobs = adzuna.fetch(settings, client=client)  # type: ignore[arg-type]

    assert jobs == []
    assert len(client.calls) == 1  # stopped after first empty page, not 5


def test_fetch_skips_gracefully_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adzuna, "load_secrets", lambda: MISSING_SECRETS)
    client = FakeFetchClient({"results": []})

    jobs = adzuna.fetch(SETTINGS, client=client)  # type: ignore[arg-type]

    assert jobs == []
    assert client.calls == []


def test_fetch_raises_when_first_request_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A total failure must propagate so the run marks Adzuna degraded."""
    monkeypatch.setattr(adzuna, "load_secrets", lambda: FAKE_SECRETS)

    class BoomClient:
        def get_json(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        adzuna.fetch(SETTINGS, client=BoomClient())  # type: ignore[arg-type]


def test_fetch_runs_each_or_term_as_separate_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adzuna ANDs every word in `what`, so 'a OR b' must become two searches."""
    monkeypatch.setattr(adzuna, "load_secrets", lambda: FAKE_SECRETS)
    settings = Settings(
        raw={"ingest": {"adzuna": {"what": "robotics OR graduate engineer", "max_pages": 1}}}
    )
    client = FakeFetchClient({"results": []})

    adzuna.fetch(settings, client=client)  # type: ignore[arg-type]

    assert len(client.calls) == 2
