from __future__ import annotations

import pytest

from jobs.ingest import greenhouse
from tests.conftest import load_fixture
from tests.ingest.conftest import FakeFetchClient


def test_fetch_parses_fixture_into_raw_jobs() -> None:
    fixture = load_fixture("greenhouse_gitlab.json")
    client = FakeFetchClient(fixture)

    jobs = greenhouse.fetch("GitLab", "gitlab", client=client)  # type: ignore[arg-type]

    assert len(jobs) == len(fixture["jobs"])
    first = jobs[0]
    raw = fixture["jobs"][0]
    assert first.source == "greenhouse"
    assert first.source_id == str(raw["id"])
    assert first.title == raw["title"].strip()
    assert first.company == raw["company_name"]
    assert first.url == raw["absolute_url"]
    assert first.location_raw == raw["location"]["name"]
    # HTML entities and tags must be stripped from the description.
    assert "<" not in (first.description or "")
    assert "&lt;" not in (first.description or "")


def test_fetch_hits_expected_url_and_params() -> None:
    client = FakeFetchClient({"jobs": []})
    greenhouse.fetch("Acme", "acme", client=client)  # type: ignore[arg-type]
    assert client.calls == [
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", {"content": "true"})
    ]


def test_fetch_degrades_to_empty_list_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomClient(FakeFetchClient):
        def get_json(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

    jobs = greenhouse.fetch("Acme", "acme", client=BoomClient({}))  # type: ignore[arg-type]
    assert jobs == []


def test_fetch_handles_non_dict_response() -> None:
    jobs = greenhouse.fetch("Acme", "acme", client=FakeFetchClient([1, 2, 3]))  # type: ignore[arg-type]
    assert jobs == []
