from __future__ import annotations

import sys
import types
from typing import Any

import pandas as pd
import pytest

from jobs.config import Settings
from jobs.ingest import jobspy_source


def _install_fake_jobspy(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    module = types.ModuleType("jobspy")

    def scrape_jobs(**kwargs: Any) -> pd.DataFrame:
        calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "site": "linkedin",
                    "id": "1",
                    "title": "Robotics Engineer",
                    "company": "Acme",
                    "location": "Melbourne",
                    "job_url": "https://example.com/1",
                    "description": "Full ad text",
                }
            ]
        )

    module.scrape_jobs = scrape_jobs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jobspy", module)


def test_fetches_linkedin_descriptions_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fake_jobspy(monkeypatch, calls)

    jobs = jobspy_source.fetch(Settings(raw={"ingest": {"jobspy": {"site_names": ["linkedin"]}}}))

    assert calls[0]["linkedin_fetch_description"] is True
    assert jobs[0].description == "Full ad text"


def test_linkedin_description_fetch_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fake_jobspy(monkeypatch, calls)
    settings = Settings(raw={"ingest": {"jobspy": {"linkedin_fetch_description": False}}})

    jobspy_source.fetch(settings)

    assert calls[0]["linkedin_fetch_description"] is False
