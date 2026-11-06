"""`jobs` CLI — every stage runnable independently, per CLAUDE.md §5."""

from __future__ import annotations

import logging
from pathlib import Path

import click
import pyperclip

from jobs.config import load_settings
from jobs.db import count_jobs, get_connection, migrate
from jobs.digest.service import run_digest
from jobs.letters.extract import extract_text
from jobs.letters.generate import run_letter
from jobs.letters.jd_fetch import fetch_job_description
from jobs.letters.style_guide import run_style_guide
from jobs.logging_utils import configure_logging, stage
from jobs.normalize.dedupe import add_manual_job
from jobs.pipeline import ingest_and_store, score_and_log

logger = logging.getLogger("jobs.cli")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Debug-level logging.")
def main(verbose: bool) -> None:
    configure_logging(logging.DEBUG if verbose else logging.INFO)


@main.command()
def ingest() -> None:
    """Fetch from every enabled source, dedupe, and store new jobs."""
    settings = load_settings()
    conn = get_connection()
    migrate(conn)

    run = ingest_and_store(conn, settings)
    ingest_result, dedupe_result = run.ingest, run.dedupe

    click.echo(
        f"Ingested {len(ingest_result.jobs)} raw jobs from "
        f"{len(ingest_result.source_counts)} sources: "
        f"{dedupe_result.new_count} new, {dedupe_result.duplicate_count} already seen."
    )
    if ingest_result.degraded_sources:
        click.echo(f"Degraded sources: {', '.join(ingest_result.degraded_sources)}")
    click.echo(f"Total jobs in db: {count_jobs(conn)}")
    conn.close()


@main.command()
@click.option("--title", required=True)
@click.option("--company", required=True)
@click.option("--url", required=True)
@click.option("--location", default=None, help="Free-text location, e.g. 'Melbourne, VIC'.")
@click.option("--description", default=None, help="Job ad text, if short enough to paste.")
@click.option(
    "--jd-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Job ad saved as pdf/docx/txt/md, for anything longer than a paste.",
)
@click.option(
    "--clipboard",
    is_flag=True,
    help="Use the job ad text on your clipboard (select it on the page, Ctrl+C, then pass this).",
)
def add(
    title: str,
    company: str,
    url: str,
    location: str | None,
    description: str | None,
    jd_file: Path | None,
    clipboard: bool,
) -> None:
    """Manually register a job you found yourself, e.g. one not covered by any ingest source.

    With none of --description/--jd-file/--clipboard, tries to fetch and
    extract the ad from --url itself; this is best-effort (JS-rendered pages,
    sites that block scraping, or robots.txt-disallowed sites like LinkedIn
    won't work) so it can fall back to asking you for one of those instead."""
    spend_usd = 0.0
    if jd_file is not None:
        description = extract_text(jd_file)
        if description is None:
            raise click.ClickException(f"could not extract text from {jd_file}")
    elif clipboard:
        description = pyperclip.paste()
        if not description or not description.strip():
            raise click.ClickException("clipboard is empty — copy the job ad text first")
        click.echo(f"Using {len(description)} chars from clipboard.")
    elif description is None:
        click.echo(f"No --description/--jd-file/--clipboard given, trying to fetch from {url} ...")
        description, spend_usd = fetch_job_description(url)
        if description is None:
            raise click.ClickException(
                "couldn't fetch/extract a job ad from that URL (blocked, JS-rendered, or not a "
                'job page). Re-run with --description "..." or --jd-file <path> instead.'
            )
        click.echo(f"Fetched {len(description)} chars. Spend: ${spend_usd:.4f}")

    settings = load_settings()
    conn = get_connection()
    migrate(conn)

    with stage(logger, "add") as result:
        job_id, is_new = add_manual_job(
            conn,
            settings,
            title=title,
            company=company,
            url=url,
            location=location,
            description=description,
        )
        result["new"] = int(is_new)

    if is_new:
        click.echo(f"Added. job_id={job_id}")
        click.echo(f"Run: uv run jobs letter {job_id}")
    else:
        click.echo(f"Already in the db (same title+company+location). job_id={job_id}")
    conn.close()


@main.command()
@click.option("--force", is_flag=True, help="Re-score jobs that already have a score.")
def score(force: bool) -> None:
    """Score unscored jobs against config/profile.md via the LLM."""
    settings = load_settings()
    conn = get_connection()
    migrate(conn)

    score_result = score_and_log(conn, settings, force=force)

    if score_result.degraded_reason and not score_result.jobs_considered:
        click.echo(f"Scoring skipped: {score_result.degraded_reason}")
    else:
        if score_result.degraded_reason:
            click.echo(f"Scoring degraded: {score_result.degraded_reason}")
        click.echo(
            f"Scored {score_result.scored_count}, skipped {score_result.skipped_count} "
            f"(considered {score_result.jobs_considered}). Spend: ${score_result.spend_usd:.4f}"
        )
        if score_result.aborted_due_to_budget:
            click.echo("Stopped early: budget cap reached.")
    conn.close()


@main.command()
def digest() -> None:
    """Send the scored, grouped digest (Discord + email) for unnotified jobs."""
    settings = load_settings()
    conn = get_connection()
    migrate(conn)

    with stage(logger, "digest") as result:
        digest_result = run_digest(conn, settings)
        result["jobs"] = digest_result.job_count
        result["discord_sent"] = int(digest_result.discord_sent)
        result["email_sent"] = int(digest_result.email_sent)

    click.echo(f"{digest_result.job_count} jobs above threshold.")
    click.echo(f"Discord: {'sent' if digest_result.discord_sent else 'not sent'}")
    click.echo(f"Email: {'sent' if digest_result.email_sent else 'not sent'}")
    conn.close()


@main.command(name="style-guide")
@click.option(
    "--force", is_flag=True, help="Regenerate an existing style guide (backs up the old one)."
)
def style_guide_cmd(force: bool) -> None:
    """Generate data/style_guide.md from your resume + inputs/cover_letters/."""
    with stage(logger, "style-guide") as result:
        sg_result = run_style_guide(force=force)
        result["generated"] = int(sg_result.generated)

    if sg_result.generated:
        click.echo(
            f"Wrote {sg_result.path} from {sg_result.letters_used} letter(s). "
            f"Spend: ${sg_result.spend_usd:.4f}"
        )
    else:
        click.echo(f"Style guide not generated: {sg_result.skipped_reason}")


@main.command()
@click.argument("job_id")
@click.option(
    "--jd-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The full job ad saved as pdf/docx/txt/md, if the stored one is cut short.",
)
def letter(job_id: str, jd_file: Path | None) -> None:
    """Generate a cover letter for a specific job id from the digest."""
    conn = get_connection()
    migrate(conn)

    with stage(logger, "letter", job_id=0) as result:
        letter_result = run_letter(conn, job_id, jd_file=jd_file)
        result["generated"] = int(letter_result.generated)

    if letter_result.generated:
        click.echo(f"Wrote {letter_result.docx_path}")
        if summary := letter_result.coverage_summary():
            click.echo(summary + (" Revised once to get there." if letter_result.revised else ""))
        if letter_result.corrected_claims:
            click.echo(
                f"Fact-check: corrected {letter_result.corrected_claims} claim(s) "
                "that didn't match your resume."
            )
        if letter_result.unverified_claims:
            click.echo("Check these against your resume before sending:")
            for claim in letter_result.unverified_claims:
                click.echo(f"  - {claim}")
        click.echo(f"Spend: ${letter_result.spend_usd:.4f}")
        if letter_result.jd_truncated:
            click.echo(
                "Note: the stored job description was cut short by its source, so this letter "
                "only saw the start of the ad. Save the full ad (text, docx or pdf) and re-run "
                "with --jd-file <path> for a better one:"
            )
            click.echo(f"  uv run jobs letter {job_id} --jd-file <path>")
    else:
        click.echo(f"Letter not generated: {letter_result.skipped_reason}")
    conn.close()


@main.command()
@click.option("--port", default=8765, show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab automatically.")
def web(port: int, no_browser: bool) -> None:
    """Open the local web UI (browse jobs, add jobs, write letters)."""
    import threading
    import webbrowser

    from jobs.web.app import create_app

    url = f"http://127.0.0.1:{port}/"
    click.echo(f"Job Seeker UI at {url}  (Ctrl+C to stop)")
    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    create_app().run(host="127.0.0.1", port=port, debug=False, threaded=True)


@main.command()
def stats() -> None:
    """Print job counts stored in the database."""
    conn = get_connection()
    migrate(conn)
    click.echo(f"Total jobs: {count_jobs(conn)}")
    conn.close()


if __name__ == "__main__":
    main()
