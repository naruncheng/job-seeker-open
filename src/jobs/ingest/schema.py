"""The common shape every ingest source normalizes into."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RawJob(BaseModel):
    """A job listing as read from a source, before dedupe/normalize/score."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Ingest source name, e.g. 'greenhouse', 'adzuna'.")
    source_id: str | None = Field(default=None, description="ID within the source, if any.")
    title: str
    company: str
    location_raw: str | None = None
    url: str
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    posted_at: str | None = Field(default=None, description="ISO 8601 date/time if known.")
    remote: bool | None = None
