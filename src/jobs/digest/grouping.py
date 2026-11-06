"""Groups scored jobs for the digest: Local -> Domestic -> International, then
industry, then role_type, per CLAUDE.md §5."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

SCOPE_ORDER = ["local", "domestic", "international"]
SCOPE_LABELS = {"local": "Local", "domestic": "Domestic", "international": "International"}

GroupedJobs = OrderedDict[str, OrderedDict[str, OrderedDict[str, list[dict[str, Any]]]]]


def group_jobs(jobs: list[dict[str, Any]]) -> GroupedJobs:
    """jobs must already be filtered to those meeting the relevance threshold."""
    grouped: GroupedJobs = OrderedDict((scope, OrderedDict()) for scope in SCOPE_ORDER)

    for job in sorted(jobs, key=lambda j: (-(j["relevance"] or 0), j["title"])):
        scope = job.get("scope") or "domestic"
        if scope not in grouped:
            grouped[scope] = OrderedDict()
        industry = job.get("industry") or "Other"
        role_type = job.get("role_type") or "Other"
        grouped[scope].setdefault(industry, OrderedDict()).setdefault(role_type, []).append(job)

    # Drop scopes/industries/role_types with no jobs so renderers don't have to.
    non_empty: GroupedJobs = OrderedDict()
    for scope, industries in grouped.items():
        if not industries:
            continue
        non_empty[scope] = industries
    return non_empty


def score_band(relevance: int) -> str:
    """A coarse band used for colour-coding, e.g. in Discord embeds."""
    if relevance >= 9:
        return "excellent"
    if relevance >= 7:
        return "good"
    return "borderline"
