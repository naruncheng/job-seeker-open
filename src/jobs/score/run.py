"""Scoring orchestrator: batches unscored jobs, calls the LLM, writes results back."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

import anthropic

from jobs.config import Settings, load_profile_text, load_secrets
from jobs.db import get_jobs_needing_score, update_job_score
from jobs.score.budget import BudgetExceeded, BudgetGuard, estimate_cost_usd
from jobs.score.llm import MODEL, ScoringUnavailable, score_batch

logger = logging.getLogger(__name__)

# Long enough to recognise the API's error, short enough for a Discord footer.
MAX_REASON_CHARS = 200


@dataclass
class ScoreRunResult:
    scored_count: int = 0
    skipped_count: int = 0
    spend_usd: float = 0.0
    aborted_due_to_budget: bool = False
    jobs_considered: int = 0
    degraded_reason: str | None = None
    scored_job_ids: list[str] = field(default_factory=list)


def _chunk(items: list[sqlite3.Row], size: int) -> list[list[sqlite3.Row]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_scoring(
    conn: sqlite3.Connection, settings: Settings, force: bool = False
) -> ScoreRunResult:
    secrets = load_secrets()
    if not secrets.anthropic_api_key:
        logger.warning("score: ANTHROPIC_API_KEY not set, skipping scoring")
        return ScoreRunResult(degraded_reason="missing ANTHROPIC_API_KEY")

    jobs = get_jobs_needing_score(conn, force=force)
    result = ScoreRunResult(jobs_considered=len(jobs))
    if not jobs:
        logger.info("score: nothing to score")
        return result

    batch_size = int(settings.get("scoring", "batch_size", default=10))
    cap_usd = float(settings.get("scoring", "budget_cap_usd", default=1.00))
    guard = BudgetGuard(cap_usd)
    profile_text = load_profile_text()
    client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)

    for batch in _chunk(jobs, batch_size):
        try:
            guard.check_before_call()
        except BudgetExceeded as exc:
            logger.warning("score: %s", exc)
            result.aborted_due_to_budget = True
            break

        try:
            scores, usage = score_batch(client, batch, profile_text)
        except ScoringUnavailable as exc:
            logger.error("score: API rejected the key, stopping: %s", exc)
            result.skipped_count = len(jobs) - result.scored_count
            result.degraded_reason = f"API key rejected: {exc}"[:MAX_REASON_CHARS]
            break
        cost = estimate_cost_usd(
            MODEL,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_input_tokens,
            usage.cache_read_input_tokens,
        )
        guard.record(cost)
        result.spend_usd = guard.spent_usd

        scored_ids = {score.job_id for score in scores}
        for score in scores:
            update_job_score(
                conn,
                job_id=score.job_id,
                relevance=score.relevance,
                scope=score.scope,
                industry=score.industry,
                role_type=score.role_type,
                visa_flag=score.visa_flag,
                reason=score.reason,
                deadline=score.deadline,
            )
        result.scored_count += len(scored_ids)
        result.scored_job_ids.extend(scored_ids)
        result.skipped_count += len(batch) - len(scored_ids)

    if result.degraded_reason is None and result.skipped_count and not result.scored_count:
        result.degraded_reason = f"all {result.skipped_count} jobs failed to score (see logs)"

    logger.info(
        "score: scored=%d skipped=%d spend=$%.4f aborted_due_to_budget=%s",
        result.scored_count,
        result.skipped_count,
        result.spend_usd,
        result.aborted_due_to_budget,
    )
    return result
