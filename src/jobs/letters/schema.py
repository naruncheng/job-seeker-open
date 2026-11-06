"""Structured output for the requirement→evidence grounding step, per
CLAUDE.md §5: claims in the letter must trace to something real on the
resume, checked before drafting starts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RequirementEvidence(BaseModel):
    requirement: str = Field(description="A requirement or preference from the job description.")
    jd_terms: list[str] = Field(
        default_factory=list,
        description=(
            "The ad's own key words/phrases for this requirement, copied exactly as the ad "
            "writes them (e.g. 'root cause analysis', 'SolidWorks', 'stakeholder engagement'). "
            "1-4 short phrases."
        ),
    )
    evidence: str | None = Field(
        default=None,
        description=(
            "Direct evidence from the resume (quoted or closely paraphrased) that the "
            "candidate meets this requirement, or null if the resume has none."
        ),
    )
    covered: bool = Field(description="Whether the resume provides real evidence for this.")
    reframe: str | None = Field(
        default=None,
        description=(
            "If covered: one sentence restating the same evidence in the ad's terms. It may "
            "change vocabulary only — never the scope, tools, outcomes or numbers. Null if "
            "not covered."
        ),
    )


class UnsupportedClaim(BaseModel):
    quote: str = Field(description="The exact text from the letter.")
    problem: str = Field(description="What the resume actually says, or that it says nothing.")


class FactCheck(BaseModel):
    unsupported: list[UnsupportedClaim]


class RequirementMapping(BaseModel):
    items: list[RequirementEvidence]
    selection_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Only if the ad explicitly asks applicants to address selection criteria: each "
            "criterion, worded as the ad words it, in the ad's order. Otherwise empty."
        ),
    )
    why_this_role: list[str] = Field(
        default_factory=list,
        description=(
            "1-3 specifics from the ad or company context (projects, sectors, focus areas, "
            "program features) that genuinely connect to something in the resume, each with "
            "the connection stated briefly."
        ),
    )
