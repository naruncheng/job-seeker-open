from __future__ import annotations

from jobs.ingest import lever
from tests.conftest import load_fixture
from tests.ingest.conftest import FakeFetchClient


def test_fetch_parses_fixture_into_raw_jobs() -> None:
    fixture = load_fixture("lever_leverdemo.json")
    client = FakeFetchClient(fixture)

    jobs = lever.fetch("Lever (demo board)", "leverdemo", client=client)  # type: ignore[arg-type]

    assert len(jobs) == len(fixture)
    first = jobs[0]
    raw = fixture[0]
    assert first.source == "lever"
    assert first.source_id == raw["id"]
    assert first.title == raw["text"].strip()
    assert first.company == "Lever (demo board)"
    assert first.url == raw["hostedUrl"]
    assert first.location_raw == raw["categories"]["location"]
    assert first.posted_at is not None
    assert first.posted_at.startswith("20")  # ISO date, not raw epoch ms


def test_fetch_hits_expected_url_and_params() -> None:
    client = FakeFetchClient([])
    lever.fetch("Acme", "acme", client=client)  # type: ignore[arg-type]
    assert client.calls == [("https://api.lever.co/v0/postings/acme", {"mode": "json"})]


def test_fetch_degrades_to_empty_list_on_error() -> None:
    class BoomClient(FakeFetchClient):
        def get_json(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

    jobs = lever.fetch("Acme", "acme", client=BoomClient({}))  # type: ignore[arg-type]
    assert jobs == []


def test_fetch_handles_non_list_response() -> None:
    jobs = lever.fetch("Acme", "acme", client=FakeFetchClient({"ok": False}))  # type: ignore[arg-type]
    assert jobs == []
