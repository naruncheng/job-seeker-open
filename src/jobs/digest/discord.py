"""Discord webhook delivery — the primary digest channel per CLAUDE.md §5.

Rich embeds, one embed per scope group, colour-coded by score band. Kept
deliberately compact for reading on a phone: two lines per job (score + linked
title, then company · sector · location · id), no description — the email
carries the full reasoning. Respects Discord's hard limits: 10 embeds / 6000
chars per message and 4096 chars per embed description, splitting across
messages when the digest is long. No bot, no buttons: read-only, link out.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from jobs.digest.dates import format_date
from jobs.digest.grouping import GroupedJobs, score_band
from jobs.digest.money import format_usd

logger = logging.getLogger(__name__)

MAX_EMBEDS_PER_MESSAGE = 10
MAX_MESSAGE_CHARS = 6000
MAX_DESCRIPTION_CHARS = 4096
MAX_FOOTER_CHARS = 2048

BAND_COLORS = {
    "excellent": 0x2ECC71,  # green
    "good": 0x3498DB,  # blue
    "borderline": 0xF1C40F,  # yellow
}
BAND_MARKERS = {"excellent": "🟢", "good": "🔵", "borderline": "🟡"}

_SCOPE_TITLES = {"local": "Local", "domestic": "Domestic", "international": "International"}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _job_entry(job: dict[str, Any]) -> str:
    """Two lines: `🔵 **8/10** [Title](url)` then
    `Company · Sector · Location · Posted 17 Sep · Closes 5 Oct · id`."""
    # Square brackets in a title would end the markdown link early, and a ")" in
    # the URL would close it early.
    title = _truncate(job["title"], 100).replace("[", "(").replace("]", ")")
    url = (job.get("url") or "").replace(")", "%29")
    heading = f"[{title}]({url})" if url else title
    marker = BAND_MARKERS[score_band(job["relevance"])]

    details = [
        _truncate(job["company"], 60),
        _truncate(job.get("industry") or "Other", 30),
    ]
    if job.get("location_raw"):
        details.append(_truncate(job["location_raw"], 40))
    if posted := format_date(job.get("posted_at")):
        details.append(f"Posted {posted}")
    if job.get("deadline"):
        details.append(f"Closes {format_date(job['deadline']) or job['deadline']}")
    details.append(f"`{job['job_id']}`")
    return f"{marker} **{job['relevance']}/10** {heading}\n" + " · ".join(details)


def _embed_char_count(embed: dict[str, Any]) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field["name"]) + len(field["value"])
    return total


def _footer_text(
    source_counts: dict[str, int],
    degraded_sources: list[str],
    scoring_cost: float | None,
    new_count: int | None = None,
) -> str:
    parts = []
    # None means no ingest run was logged, so we don't know — say nothing
    # rather than imply zero new listings were found.
    if new_count is not None:
        parts.append(f"{new_count} new listing{'' if new_count == 1 else 's'}")
    parts.append(f"Sources: {', '.join(f'{k}={v}' for k, v in source_counts.items()) or 'none'}")
    if (cost := format_usd(scoring_cost)) is not None:
        parts.append(f"Scoring: {cost}")
    if degraded_sources:
        parts.append(f"Degraded: {', '.join(degraded_sources)}")
    return _truncate(" | ".join(parts), MAX_FOOTER_CHARS)


def build_embeds(
    grouped: GroupedJobs,
    source_counts: dict[str, int],
    degraded_sources: list[str],
    scoring_cost: float | None = None,
    new_count: int | None = None,
) -> list[dict[str, Any]]:
    """One embed per non-empty scope group, split further if it exceeds limits."""
    embeds: list[dict[str, Any]] = []
    footer_text = _footer_text(source_counts, degraded_sources, scoring_cost, new_count)
    # A whole message is capped at 6000 chars. Reserve room for the footer (only
    # the last embed carries it, but reserving uniformly is simpler) and a title.
    description_budget = min(MAX_DESCRIPTION_CHARS, MAX_MESSAGE_CHARS - len(footer_text) - 100)

    for scope, industries in grouped.items():
        jobs = [
            job
            for role_types in industries.values()
            for role_jobs in role_types.values()
            for job in role_jobs
        ]
        if not jobs:
            continue
        band_order = ["borderline", "good", "excellent"]
        band = max((score_band(job["relevance"]) for job in jobs), key=band_order.index)
        title = f"{_SCOPE_TITLES.get(scope, scope.title())} ({len(jobs)})"

        chunks: list[list[str]] = [[]]
        chunk_chars = 0
        for entry in (_job_entry(job) for job in jobs):
            entry_chars = len(entry) + 2  # blank line between jobs
            if chunks[-1] and chunk_chars + entry_chars > description_budget:
                chunks.append([])
                chunk_chars = 0
            chunks[-1].append(entry)
            chunk_chars += entry_chars

        for n, chunk in enumerate(chunks):
            embeds.append(
                {
                    "title": title if n == 0 else title + " (cont.)",
                    "description": "\n\n".join(chunk),
                    "color": BAND_COLORS[band],
                }
            )

    if embeds:
        embeds[-1]["footer"] = {"text": footer_text}

    return embeds


def paginate_messages(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Pack embeds into messages respecting the 10-embed / 6000-char limits."""
    messages: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    for embed in embeds:
        embed_chars = _embed_char_count(embed)
        over_embed_limit = len(current) >= MAX_EMBEDS_PER_MESSAGE
        over_char_limit = current_chars + embed_chars > MAX_MESSAGE_CHARS
        if current and (over_embed_limit or over_char_limit):
            messages.append(current)
            current, current_chars = [], 0
        current.append(embed)
        current_chars += embed_chars

    if current:
        messages.append(current)
    return messages


def send_discord_digest(
    webhook_url: str,
    grouped: GroupedJobs,
    source_counts: dict[str, int],
    degraded_sources: list[str],
    run_date: str | None = None,
    scoring_cost: float | None = None,
    new_count: int | None = None,
) -> bool:
    """Returns True if every message was posted successfully.

    `run_date` (e.g. 'Monday 21 September 2026') is posted as a heading above
    the first message; message `content` sits outside the 6000-char embed limit.
    """
    embeds = build_embeds(grouped, source_counts, degraded_sources, scoring_cost, new_count)

    if not embeds:
        footer = _footer_text(source_counts, degraded_sources, scoring_cost, new_count)
        if degraded_sources:
            title = "Nothing to show — part of the pipeline failed"
            description = (
                "No qualifying listings, but this may be incomplete: "
                "see Degraded in the footer."
            )
            color = 0xE67E22
        else:
            title = "Nothing new today"
            description = "The pipeline ran and found no new qualifying listings."
            color = 0x95A5A6
        messages = [
            [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "footer": {"text": _truncate(footer, MAX_FOOTER_CHARS)},
                }
            ]
        ]
    else:
        messages = paginate_messages(embeds)

    ok = True
    with httpx.Client(timeout=20) as client:
        for i, message_embeds in enumerate(messages):
            if i > 0:
                time.sleep(1)
            heading = f"📋 **Job digest — {run_date}**" if run_date and i == 0 else None
            if not _post_with_retry(client, webhook_url, message_embeds, content=heading):
                ok = False
    return ok


def _post_with_retry(
    client: httpx.Client,
    webhook_url: str,
    embeds: list[dict[str, Any]],
    content: str | None = None,
) -> bool:
    payload: dict[str, Any] = {"embeds": embeds}
    if content:
        payload["content"] = content
    for attempt in range(3):
        try:
            resp = client.post(webhook_url, json=payload)
        except httpx.HTTPError:
            logger.exception("discord: request error on attempt %d", attempt + 1)
            time.sleep(2**attempt)
            continue

        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("retry-after", "1"))
            logger.warning("discord: rate limited, retrying after %.1fs", retry_after)
            time.sleep(retry_after)
            continue
        logger.error("discord: send failed with status %d: %s", resp.status_code, resp.text[:300])
        return False

    logger.error("discord: gave up after retries")
    return False
