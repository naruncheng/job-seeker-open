from __future__ import annotations

from jobs.config import Secrets
from jobs.digest.email import render_digest_email, send_email_digest
from jobs.digest.grouping import group_jobs

INCOMPLETE_SECRETS = Secrets(
    anthropic_api_key=None,
    adzuna_app_id=None,
    adzuna_app_key=None,
    discord_webhook_url=None,
    smtp_host=None,
    smtp_port=587,
    smtp_user=None,
    smtp_pass=None,
    digest_to_email=None,
    digest_from_email=None,
    user_agent_email=None,
)


def _job(**overrides: object) -> dict:
    defaults = dict(
        job_id="abc123",
        title="Robotics Engineer",
        company="Acme",
        url="https://example.com/1",
        relevance=8,
        scope="local",
        industry="Robotics",
        role_type="Graduate Program",
        reason="Good fit",
        deadline=None,
        visa_flag=None,
        location_raw="Melbourne",
        source="greenhouse",
    )
    defaults.update(overrides)
    return defaults


def test_render_digest_email_lists_jobs() -> None:
    grouped = group_jobs([_job()])
    html = render_digest_email(grouped, 1, {"greenhouse:acme": 1}, [])
    assert "Robotics Engineer" in html
    assert "1 job" in html


def test_render_digest_email_nothing_new() -> None:
    html = render_digest_email(group_jobs([]), 0, {}, [])
    assert "Nothing new today" in html


def test_render_digest_email_shows_degraded_sources() -> None:
    html = render_digest_email(group_jobs([]), 0, {}, ["jobspy"])
    assert "jobspy" in html


def test_send_email_digest_skips_without_smtp_config() -> None:
    sent = send_email_digest(INCOMPLETE_SECRETS, group_jobs([]), 0, {}, [])
    assert sent is False


def test_render_digest_email_escapes_scraped_html() -> None:
    grouped = group_jobs([_job(title="<script>alert(1)</script> & Co", reason="<b>bold</b>")])
    html = render_digest_email(grouped, 1, {}, [])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html


def test_render_digest_email_keeps_all_job_details() -> None:
    grouped = group_jobs(
        [_job(
            deadline="2020-04-05",
            posted_at="2020-03-04T10:00:00Z",
            visa_flag="sponsorship offered",
            reason="Strong fit",
        )]
    )
    html = render_digest_email(grouped, 1, {"adzuna": 1}, [])
    for expected in (
        "Robotics Engineer",
        "Acme",
        "Melbourne",
        "8/10",
        "Strong fit",
        "Closes 5 Apr 2020",
        "Posted 4 Mar 2020",
        "sponsorship offered",
        "abc123",
        "https://example.com/1",
        "Local",
    ):
        assert expected in html


def test_render_digest_email_shows_the_run_date_with_and_without_jobs() -> None:
    date = "Monday 21 September 2026"
    with_jobs = render_digest_email(group_jobs([_job()]), 1, {}, [], run_date=date)
    empty = render_digest_email(group_jobs([]), 0, {}, [], run_date=date)
    assert date in with_jobs
    assert date in empty
    assert date not in render_digest_email(group_jobs([_job()]), 1, {}, [])


def test_render_digest_email_shows_scoring_cost_beside_sources() -> None:
    html = render_digest_email(group_jobs([_job()]), 1, {"adzuna": 5}, [], scoring_cost=0.02893)
    assert "Sources this run: adzuna=5" in html
    assert "Scoring cost: $0.029" in html


def test_render_digest_email_omits_scoring_cost_when_unknown() -> None:
    html = render_digest_email(group_jobs([_job()]), 1, {"adzuna": 5}, [])
    assert "Scoring cost" not in html


def test_render_digest_email_reports_new_listing_count() -> None:
    html = render_digest_email(group_jobs([_job()]), 1, {"adzuna": 5}, [], new_count=23)
    assert "23 new listings seen overnight" in html
    assert "1 of them above threshold" in html


def test_render_digest_email_omits_new_count_when_no_ingest_was_logged() -> None:
    html = render_digest_email(group_jobs([_job()]), 1, {"adzuna": 5}, [])
    assert "seen overnight" not in html


def test_render_digest_email_reports_zero_new_listings() -> None:
    html = render_digest_email(group_jobs([]), 0, {}, [], new_count=0)
    assert "0 new listings seen overnight" in html
    # No jobs above threshold, so that clause is dropped rather than saying "0 of them".
    assert "above threshold" not in html


def test_render_digest_email_uses_singular_for_one_new_listing() -> None:
    html = render_digest_email(group_jobs([]), 0, {}, [], new_count=1)
    assert "1 new listing seen overnight" in html
