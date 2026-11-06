"""Scored + grouped HTML email digest — the durable archive / fallback
channel per CLAUDE.md §5 (Discord is primary)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobs.config import Secrets
from jobs.digest.dates import format_date
from jobs.digest.grouping import SCOPE_LABELS, GroupedJobs, score_band
from jobs.digest.money import format_usd

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_digest_email(
    grouped: GroupedJobs,
    total_count: int,
    source_counts: dict[str, int],
    degraded_sources: list[str],
    run_date: str | None = None,
    scoring_cost: float | None = None,
    new_count: int | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        # The template is named digest.html.j2, so the default "html" extension
        # match doesn't apply; job titles/reasons come from scraped pages.
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("digest.html.j2")
    source_counts_desc = ", ".join(f"{k}={v}" for k, v in source_counts.items()) or "none"
    scope_counts = {
        scope: sum(len(jobs) for role_types in industries.values() for jobs in role_types.values())
        for scope, industries in grouped.items()
    }
    return template.render(
        grouped=grouped,
        scope_counts=scope_counts,
        total_count=total_count,
        scope_labels=SCOPE_LABELS,
        score_band=score_band,
        format_date=format_date,
        run_date=run_date,
        scoring_cost_desc=format_usd(scoring_cost),
        source_counts_desc=source_counts_desc,
        degraded_sources=degraded_sources,
        new_count=new_count,
    )


def send_email_digest(
    secrets: Secrets,
    grouped: GroupedJobs,
    total_count: int,
    source_counts: dict[str, int],
    degraded_sources: list[str],
    run_date: str | None = None,
    scoring_cost: float | None = None,
    new_count: int | None = None,
) -> bool:
    """Degrades gracefully (logs + returns False) if SMTP isn't configured."""
    missing = secrets.describe_missing(["smtp_host", "smtp_user", "smtp_pass", "digest_to_email"])
    if missing:
        logger.warning("email digest: skipping, missing config: %s", ", ".join(missing))
        return False
    assert secrets.smtp_host is not None
    assert secrets.smtp_user is not None
    assert secrets.smtp_pass is not None
    assert secrets.digest_to_email is not None

    html_body = render_digest_email(
        grouped,
        total_count,
        source_counts,
        degraded_sources,
        run_date=run_date,
        scoring_cost=scoring_cost,
        new_count=new_count,
    )
    stamp = f" ({run_date})" if run_date else ""
    subject = (
        f"Job digest{stamp}: {total_count} new"
        if total_count
        else f"Job digest{stamp}: nothing new today"
    )
    if degraded_sources:
        subject += " (degraded)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = secrets.digest_from_email or secrets.smtp_user
    msg["To"] = secrets.digest_to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(secrets.smtp_host, secrets.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(secrets.smtp_user, secrets.smtp_pass)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        logger.exception("email digest: send failed")
        return False

    logger.info("email digest: sent (%d jobs)", total_count)
    return True
