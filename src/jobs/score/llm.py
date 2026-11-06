"""Batch relevance scoring + classification via the Claude Messages API.

Per CLAUDE.md §5/§10: batch ~10 jobs per call, validate every response with
pydantic, retry once on failure then log and skip. Haiku is used for this
bulk classification work (Sonnet is reserved for cover letters, a later
increment). The scoring instructions + profile are stable across every batch
in a run, so they're sent as a cached system prompt to cut repeat-call cost.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import anthropic
from anthropic.types import TextBlockParam
from anthropic.types.cache_control_ephemeral_param import CacheControlEphemeralParam
from pydantic import ValidationError

from jobs.score.schema import BatchScoreResult, JobScore

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4096
MAX_DESCRIPTION_CHARS = 1200

SYSTEM_INSTRUCTIONS = """\
You score job listings for relevance against a candidate's profile.

For each job, return:
- relevance: 0-10 (0 = totally irrelevant/excluded, 10 = perfect fit)
- scope: "local" (in the candidate's preferred city), "domestic" (rest of \
their country), or "international" (elsewhere)
- industry: a short label (e.g. "Robotics", "Manufacturing", "Software")
- role_type: a short label (e.g. "Graduate Program", "Internship", \
"Junior Engineer", "R&D")
- visa_flag: a short note on visa/sponsorship if the listing mentions it or \
it's relevant for an international listing, else null
- reason: one sentence, specific and quote-backed where possible, not generic
- deadline: an ISO date if the listing states an application deadline, else null

Apply the candidate's hard excludes as a relevance of 0-1, not by omitting \
the job. When experience level is ambiguous, don't auto-exclude — score it \
and explain the ambiguity in the reason.

Candidate profile:
"""


class ScoringUnavailable(RuntimeError):
    """The API rejected the account/key itself, so every batch would fail the same way.

    Raised instead of retrying so the run stops after one request and the
    digest can say scoring is broken, rather than reporting "nothing new".
    """


def _is_account_error(exc: Exception) -> bool:
    """401/403, or a 400 about the API key (e.g. a key not scoped to a workspace)."""
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    return isinstance(exc, anthropic.BadRequestError) and "api key" in str(exc).lower()


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def _format_job(row: sqlite3.Row) -> str:
    description = (row["description"] or "")[:MAX_DESCRIPTION_CHARS]
    return (
        f"job_id: {row['job_id']}\n"
        f"title: {row['title']}\n"
        f"company: {row['company']}\n"
        f"location: {row['location_raw'] or 'unknown'}\n"
        f"description: {description}\n"
    )


def _build_user_message(jobs: list[sqlite3.Row]) -> str:
    listings = "\n---\n".join(_format_job(job) for job in jobs)
    return f"Score these {len(jobs)} job listings:\n\n{listings}"


def score_batch(
    client: anthropic.Anthropic, jobs: list[sqlite3.Row], profile_text: str
) -> tuple[list[JobScore], Usage]:
    """Score one batch. Returns ([], usage-of-last-attempt) if both attempts fail.

    Raises ScoringUnavailable when the API rejects the key itself.
    """
    expected_ids = {job["job_id"] for job in jobs}
    system: list[TextBlockParam] = [
        TextBlockParam(
            type="text",
            text=SYSTEM_INSTRUCTIONS + profile_text,
            cache_control=CacheControlEphemeralParam(type="ephemeral"),
        )
    ]
    user_message = _build_user_message(jobs)

    last_usage = Usage(0, 0, 0, 0)
    for attempt in (1, 2):
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                output_format=BatchScoreResult,
            )
        except (anthropic.APIError, ValidationError) as exc:
            if _is_account_error(exc):
                raise ScoringUnavailable(str(exc)) from exc
            logger.warning("score batch: attempt %d failed: %s", attempt, exc)
            continue

        usage = response.usage
        last_usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

        result = response.parsed_output
        if result is None:
            logger.warning("score batch: attempt %d returned no parsed output", attempt)
            continue
        returned_ids = {score.job_id for score in result.scores}
        if returned_ids != expected_ids:
            logger.warning(
                "score batch: attempt %d returned mismatched job ids (expected %d, got %d)",
                attempt,
                len(expected_ids),
                len(returned_ids),
            )
            continue

        return result.scores, last_usage

    logger.error("score batch: failed after retry, skipping %d jobs", len(jobs))
    return [], last_usage
