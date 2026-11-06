from __future__ import annotations

from jobs.ingest import ashby
from tests.conftest import load_fixture
from tests.ingest.conftest import FakeFetchClient


def test_fetch_parses_fixture_into_raw_jobs() -> None:
    fixture = load_fixture("ashby_ramp.json")
    client = FakeFetchClient(fixture)

    jobs = ashby.fetch("Ramp", "ramp", client=client)  # type: ignore[arg-type]

    assert len(jobs) == len(fixture["jobs"])
    first = jobs[0]
    raw = fixture["jobs"][0]
    assert first.source == "ashby"
    assert first.source_id == raw["id"]
    assert first.title == raw["title"].strip()
    assert first.company == "Ramp"
    assert first.url == raw["jobUrl"]
    assert first.location_raw == raw["location"]
    assert first.remote == raw["isRemote"]
    assert "<" not in (first.description or "")


def test_fetch_hits_expected_url() -> None:
    client = FakeFetchClient({"jobs": []})
    ashby.fetch("Acme", "acme", client=client)  # type: ignore[arg-type]
    assert client.calls == [("https://api.ashbyhq.com/posting-api/job-board/acme", None)]


def test_fetch_degrades_to_empty_list_on_error() -> None:
    class BoomClient(FakeFetchClient):
        def get_json(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

    jobs = ashby.fetch("Acme", "acme", client=BoomClient({}))  # type: ignore[arg-type]
    assert jobs == []
