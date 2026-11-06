from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

from jobs.db import get_connection, get_job, upsert_job
from jobs.letters import generate
from jobs.letters.generate import LetterResult
from jobs.web import app as app_module
from jobs.web.app import create_app

JOB_ID = "0123456789abcdef"


def _insert(conn: sqlite3.Connection, job_id: str = JOB_ID, **overrides: Any) -> None:
    fields: dict[str, Any] = {
        "job_id": job_id,
        "job_id_full": job_id * 4,
        "source": "adzuna",
        "source_id": "1",
        "title": "Graduate Robotics Engineer",
        "company": "Acme Robotics",
        "location_raw": "Melbourne VIC",
        "location_city": None,
        "location_country": None,
        "remote": None,
        "url": "https://example.com/job/1",
        "description": "Build robots. Requires a mechatronics degree.",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "posted_at": None,
        "raw_payload": None,
    }
    fields.update(overrides)
    upsert_job(conn, **fields)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    letters = tmp_path / "letters"
    letters.mkdir()
    return tmp_path / "jobs.db", letters


@pytest.fixture
def client(paths: tuple[Path, Path]) -> FlaskClient:
    db_path, letters = paths
    app = create_app(db_path=db_path, letters_dir=letters)
    conn = get_connection(db_path)
    _insert(conn)
    _insert(
        conn,
        job_id="fedcba9876543210",
        title="Sales Manager",
        company="Other Co",
        source="jobspy:linkedin",
    )
    conn.close()
    return app.test_client()


def test_list_shows_jobs_and_filters_by_search(client: FlaskClient) -> None:
    page = client.get("/").get_data(as_text=True)
    assert "Graduate Robotics Engineer" in page
    assert "Sales Manager" in page

    page = client.get("/?q=robot").get_data(as_text=True)
    assert "Graduate Robotics Engineer" in page
    assert "Sales Manager" not in page


def test_list_filters_by_source(client: FlaskClient) -> None:
    page = client.get("/?source=jobspy").get_data(as_text=True)
    assert "Sales Manager" in page
    assert "Graduate Robotics Engineer" not in page


def test_job_detail_and_unknown_or_malformed_ids_404(client: FlaskClient) -> None:
    page = client.get(f"/job/{JOB_ID}").get_data(as_text=True)
    assert "Acme Robotics" in page
    assert "Write cover letter" in page
    assert client.get("/job/ffffffffffffffff").status_code == 404
    assert client.get("/job/..%2F..%2Fetc").status_code == 404


def test_add_with_pasted_text_saves_and_redirects(
    client: FlaskClient, paths: tuple[Path, Path]
) -> None:
    resp = client.post(
        "/add",
        data={
            "title": "Graduate Mechatronics Engineer",
            "company": "SRLA",
            "url": "https://www.linkedin.com/jobs/view/123",
            "location": "Melbourne",
            "description": "Full pasted ad text.",
        },
    )
    assert resp.status_code == 302
    job_id = resp.headers["Location"].rsplit("/", 1)[-1]
    conn = get_connection(paths[0])
    job = get_job(conn, job_id)
    conn.close()
    assert job is not None
    assert job["source"] == "manual"
    assert job["description"] == "Full pasted ad text."


def test_add_without_text_falls_back_to_paste_prompt_when_fetch_fails(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "fetch_job_description", lambda url: (None, 0.0))
    resp = client.post(
        "/add",
        data={"title": "T", "company": "C", "url": "https://www.linkedin.com/jobs/view/1"},
    )
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Paste the ad text" in page
    assert 'value="C"' in page  # form keeps what was typed


def test_add_rejects_non_http_links(client: FlaskClient) -> None:
    resp = client.post(
        "/add",
        data={"title": "T", "company": "C", "url": "javascript:alert(1)", "description": "x"},
    )
    assert resp.status_code == 200
    assert "should start with http" in resp.get_data(as_text=True)


def test_letter_saves_pasted_ad_then_generates(
    client: FlaskClient, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, letters = paths
    seen: dict[str, Any] = {}

    def fake_run_letter(conn: sqlite3.Connection, job_id: str) -> LetterResult:
        seen["description"] = get_job(conn, job_id)["description"]  # type: ignore[index]
        md = letters / f"{job_id}.md"
        md.write_text("# Title — Co\n\nTo the hiring manager,\n\nSecond para.\n", encoding="utf-8")
        return LetterResult(
            generated=True,
            markdown_path=md,
            spend_usd=0.04,
            terms_used=["CAD", "robotics"],
            terms_missed=["ROS"],
        )

    monkeypatch.setattr(generate, "run_letter", fake_run_letter)
    resp = client.post(f"/job/{JOB_ID}/letter", data={"description": "The full ad."})
    assert resp.status_code == 302
    assert seen["description"] == "The full ad."

    page = client.get(f"/job/{JOB_ID}").get_data(as_text=True)
    assert "To the hiring manager," in page
    assert "Title — Co" not in page  # heading line stripped from the letter body
    assert "2/3 requirements you meet" in page
    assert "Download .docx" in page

    download = client.get(f"/job/{JOB_ID}/letter.docx")
    assert download.status_code == 200
    assert download.data[:2] == b"PK"  # a real .docx (zip), built from the saved letter
    expected = f"Cover Letter Acme Robotics {date.today().year}.docx"
    assert expected in download.headers["Content-Disposition"]
    assert client.get(f"/job/{JOB_ID}/letter.exe").status_code == 404


def test_letter_failure_is_shown_not_raised(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        generate,
        "run_letter",
        lambda conn, job_id: LetterResult(skipped_reason="missing ANTHROPIC_API_KEY"),
    )
    resp = client.post(f"/job/{JOB_ID}/letter", follow_redirects=True)
    assert "missing ANTHROPIC_API_KEY" in resp.get_data(as_text=True)


def test_truncated_ad_warns(paths: tuple[Path, Path]) -> None:
    db_path, letters = paths
    app = create_app(db_path=db_path, letters_dir=letters)
    conn = get_connection(db_path)
    _insert(conn, description="Short teaser of the ad...")
    conn.close()
    page = app.test_client().get(f"/job/{JOB_ID}").get_data(as_text=True)
    assert "Only the start of this ad was saved" in page


def test_cross_origin_post_is_refused(client: FlaskClient) -> None:
    resp = client.post(f"/job/{JOB_ID}/letter", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_untrusted_host_is_refused(client: FlaskClient) -> None:
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_ingest_button_reports_counts(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from jobs.ingest.run import IngestResult
    from jobs.normalize.dedupe import DedupeResult
    from jobs.pipeline import IngestRun

    fake = IngestRun(
        ingest=IngestResult(), dedupe=DedupeResult(new_job_ids=["a", "b"], duplicate_count=5)
    )
    monkeypatch.setattr(app_module, "ingest_and_store", lambda conn, settings: fake)
    resp = client.post("/ingest", follow_redirects=True)
    assert "2 new, 5 already seen" in resp.get_data(as_text=True)
