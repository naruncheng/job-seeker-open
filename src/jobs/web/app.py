"""Local web UI over the same db, stages and outputs as the CLI.

Binds to localhost only and is meant for one person at their own PC; it is
not a hosted service. POSTs from other origins are refused so a random web
page can't make the browser trigger paid LLM calls here.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.wrappers import Response

from jobs.config import load_settings
from jobs.db import (
    DEFAULT_DB_PATH,
    count_jobs,
    get_connection,
    get_job,
    get_latest_run_time,
    list_jobs,
    migrate,
    update_description,
)
from jobs.digest.dates import format_date
from jobs.letters import generate
from jobs.letters.generate import looks_truncated
from jobs.letters.jd_fetch import fetch_job_description
from jobs.normalize.dedupe import add_manual_job
from jobs.pipeline import ingest_and_store, score_and_log

PAGE_SIZE = 50
_JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SOURCES = ("manual", "adzuna", "jobspy")
SORTS = {"newest": "Newest", "score": "Score", "company": "Company"}
ADD_FIELDS = ("title", "company", "url", "location", "description")
DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|])")


def unescape_markdown(text: str | None) -> str:
    """Job-board ads arrive markdown-escaped ('entry\\-level'); show them plainly."""
    return _MD_ESCAPE_RE.sub(r"\1", text or "")


def create_app(db_path: Path = DEFAULT_DB_PATH, letters_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1"]
    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["plain"] = unescape_markdown

    def letters_path() -> Path:
        return letters_dir or generate.LETTERS_DIR

    init_conn = get_connection(db_path)
    migrate(init_conn)
    init_conn.close()

    def db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = get_connection(db_path)
        conn: sqlite3.Connection = g.db
        return conn

    @app.teardown_appcontext
    def close_db(_exc: BaseException | None) -> None:
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.before_request
    def refuse_cross_origin_posts() -> None:
        if request.method != "POST":
            return
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)

    def require_job(job_id: str) -> dict[str, Any]:
        if not _JOB_ID_RE.match(job_id):
            abort(404)
        job = get_job(db(), job_id)
        if job is None:
            abort(404)
        return job

    def letter_body(job_id: str) -> str | None:
        md_path = letters_path() / f"{job_id}.md"
        if not md_path.exists():
            return None
        lines = md_path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        return "\n".join(lines).strip()

    @app.get("/")
    def jobs_list() -> str:
        query = request.args.get("q", "").strip()
        source = request.args.get("source", "")
        sort = request.args.get("sort", "newest")
        min_score = request.args.get("min", "")
        page = max(request.args.get("page", 1, type=int) or 1, 1)

        rows, total = list_jobs(
            db(),
            query=query,
            min_relevance=int(min_score) if min_score.isdigit() else None,
            source=source if source in SOURCES else None,
            sort=sort if sort in SORTS else "newest",
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
        lettered = {p.stem for p in letters_path().glob("*.md")}
        return render_template(
            "jobs.html",
            rows=rows,
            total=total,
            all_count=count_jobs(db()),
            page=page,
            pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
            lettered=lettered,
            filters={"q": query, "source": source, "sort": sort, "min": min_score},
            sources=SOURCES,
            sorts=SORTS,
            last_ingest=get_latest_run_time(db(), "ingest"),
        )

    @app.post("/ingest")
    def ingest() -> Response:
        run = ingest_and_store(db(), load_settings())
        message = (
            f"Fetched {len(run.ingest.jobs)} listings: {run.dedupe.new_count} new, "
            f"{run.dedupe.duplicate_count} already seen."
        )
        if run.ingest.degraded_sources:
            message += f" Failed sources: {', '.join(run.ingest.degraded_sources)}."
        flash(message, "ok")
        return redirect(url_for("jobs_list"))

    @app.post("/score")
    def score() -> Response:
        result = score_and_log(db(), load_settings())
        if result.degraded_reason and not result.jobs_considered:
            flash(f"Scoring skipped: {result.degraded_reason}", "error")
        else:
            flash(
                f"Scored {result.scored_count} jobs (${result.spend_usd:.3f}).",
                "error" if result.degraded_reason else "ok",
            )
        return redirect(url_for("jobs_list", sort="score"))

    @app.route("/add", methods=["GET", "POST"])
    def add() -> str | Response:
        form = {k: request.form.get(k, "").strip() for k in ADD_FIELDS}
        if request.method == "GET":
            return render_template("add.html", form=form)

        missing = [k for k in ("title", "company", "url") if not form[k]]
        if missing:
            flash(f"Please fill in: {', '.join(missing)}.", "error")
            return render_template("add.html", form=form)
        if urlparse(form["url"]).scheme not in ("http", "https"):
            flash("The link should start with http:// or https://.", "error")
            return render_template("add.html", form=form)

        description = form["description"] or None
        if description is None:
            description, spend = fetch_job_description(form["url"])
            if description is None:
                flash(
                    "Couldn't read the ad from that link (LinkedIn, Indeed and most job boards "
                    "block this). Paste the ad text into the box below instead.",
                    "error",
                )
                return render_template("add.html", form=form, focus_description=True)
            flash(f"Read the ad from the link (${spend:.4f}).", "ok")

        job_id, is_new = add_manual_job(
            db(),
            load_settings(),
            title=form["title"],
            company=form["company"],
            url=form["url"],
            location=form["location"] or None,
            description=description,
        )
        if not is_new:
            flash("That job was already saved — here it is.", "ok")
        return redirect(url_for("job_detail", job_id=job_id))

    @app.get("/job/<job_id>")
    def job_detail(job_id: str) -> str:
        job = require_job(job_id)
        body = letter_body(job_id)
        letter_paragraphs = (
            [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()] if body else []
        )
        return render_template(
            "job.html",
            job=job,
            truncated=looks_truncated(job["description"]),
            letter_paragraphs=letter_paragraphs,
        )

    @app.post("/job/<job_id>/letter")
    def make_letter(job_id: str) -> Response:
        require_job(job_id)
        pasted = request.form.get("description", "").strip()
        if pasted:
            update_description(db(), job_id, pasted)
        result = generate.run_letter(db(), job_id)
        if result.generated:
            flash(f"Letter written (${result.spend_usd:.3f}). Read it before you send it.", "ok")
            if summary := result.coverage_summary():
                flash(summary, "ok")
            if result.corrected_claims:
                flash(
                    f"Fact-check corrected {result.corrected_claims} claim(s) that didn't "
                    "match your resume.",
                    "ok",
                )
            if result.unverified_claims:
                flash(
                    "Check these against your resume before sending: "
                    + " | ".join(result.unverified_claims),
                    "error",
                )
        else:
            flash(f"Letter not generated: {result.skipped_reason}", "error")
        return redirect(url_for("job_detail", job_id=job_id) + "#letter")

    @app.get("/job/<job_id>/letter.<ext>")
    def download_letter(job_id: str, ext: str) -> Response:
        job = require_job(job_id)
        if ext not in ("md", "docx"):
            abort(404)
        body = letter_body(job_id)
        if body is None:
            abort(404)
        stem = generate.letter_filename(job["company"], date.today().year).removesuffix(".docx")
        if ext == "md":
            md_path = letters_path() / f"{job_id}.md"
            return send_file(md_path.resolve(), as_attachment=True, download_name=f"{stem}.md")
        # Built from the saved letter text, so it works even if the .docx was moved.
        return send_file(
            io.BytesIO(generate.docx_bytes(body, generate.author_name(), generate.portfolio_url())),
            as_attachment=True,
            download_name=f"{stem}.docx",
            mimetype=DOCX_MIMETYPE,
        )

    return app
