"""What the scorer returns per job, per CLAUDE.md §5."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["local", "domestic", "international"]


class JobScore(BaseModel):
    job_id: str = Field(description="The short job id this score belongs to.")
    relevance: int = Field(ge=0, le=10, description="0 (irrelevant) to 10 (perfect fit).")
    scope: Scope
    industry: str
    role_type: str
    visa_flag: str | None = Field(
        default=None,
        description=(
            "Visa/sponsorship note if relevant (e.g. 'sponsorship not offered'), else null."
        ),
    )
    reason: str = Field(description="One sentence explaining the score.")
    deadline: str | None = Field(default=None, description="ISO date if a deadline is stated.")


class BatchScoreResult(BaseModel):
    scores: list[JobScore]
