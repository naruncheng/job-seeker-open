from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from jobs.config import Secrets
from jobs.db import upsert_job
from jobs.letters import generate as generate_module
from jobs.letters.schema import (
    FactCheck,
    RequirementEvidence,
    RequirementMapping,
    UnsupportedClaim,
)

SECRETS_WITH_KEY = Secrets(
    anthropic_api_key="fake-key",
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

SECRETS_NO_KEY = Secrets(
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

MAPPING = RequirementMapping(
    items=[
        RequirementEvidence(
            requirement="3+ years Python", evidence="Built X in Python for 3 years", covered=True
        ),
        RequirementEvidence(requirement="PhD required", evidence=None, covered=False),
    ]
)


def _insert_job(conn: sqlite3.Connection, job_id: str) -> None:
    upsert_job(
        conn,
        job_id=job_id,
        job_id_full=job_id * 4,
        source="greenhouse",
        source_id="1",
        title="Robotics Engineer",
        company="Acme",
        location_raw="Melbourne",
        location_city="Melbourne",
        location_country="Australia",
        remote=False,
        url="https://example.com/jobs/1",
        description="Build robots. Requires 3+ years Python.",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        raw_payload="{}",
    )


def _setup_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(generate_module, "STYLE_GUIDE_PATH", tmp_path / "style_guide.md")
    monkeypatch.setattr(generate_module, "RESUME_PATH", tmp_path / "resume.txt")
    monkeypatch.setattr(generate_module, "COVER_LETTERS_DIR", tmp_path / "cover_letters")
    monkeypatch.setattr(generate_module, "LETTERS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(generate_module, "fetch_company_context", lambda url: None)


def _setup_valid_inputs(tmp_path: Path) -> None:
    (tmp_path / "style_guide.md").write_text("Be concise and warm.", encoding="utf-8")
    (tmp_path / "resume.txt").write_text("Resume: 3 years Python experience.", encoding="utf-8")
    letters_dir = tmp_path / "cover_letters"
    letters_dir.mkdir()
    (letters_dir / "letter1.txt").write_text("Dear hiring manager, robotics...", encoding="utf-8")


class FakeMessages:
    def __init__(self, parse_responses: list[Any], create_responses: list[Any]) -> None:
        self._parse_responses = list(parse_responses)
        self._create_responses = list(create_responses)

    def parse(self, **kwargs: Any) -> Any:
        if not self._parse_responses and kwargs.get("output_format") is FactCheck:
            return _factcheck_response()  # every fact-check passes unless a test queues one
        response = self._parse_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def create(self, **kwargs: Any) -> Any:
        response = self._create_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _factcheck_response(*quotes: str) -> SimpleNamespace:
    claims = [UnsupportedClaim(quote=q, problem="not on the resume") for q in quotes]
    return SimpleNamespace(
        parsed_output=FactCheck(unsupported=claims),
        usage=SimpleNamespace(input_tokens=300, output_tokens=50),
    )


def _ok_mapping_response() -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=MAPPING, usage=SimpleNamespace(input_tokens=500, output_tokens=200)
    )


def _ok_draft_response(
    text: str = "Dear Acme, I am a great fit.", stop_reason: str = "end_turn"
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=800, output_tokens=300),
        stop_reason=stop_reason,
    )


def _fake_client(parse_responses: list[Any], create_responses: list[Any]) -> Any:
    client = SimpleNamespace()
    client.messages = FakeMessages(parse_responses, create_responses)
    return client


def test_run_letter_skips_without_api_key(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_NO_KEY)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is False
    assert result.skipped_reason == "missing ANTHROPIC_API_KEY"


def test_run_letter_skips_when_job_not_found(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)

    result = generate_module.run_letter(db_conn, "does-not-exist")

    assert result.generated is False
    assert "no job found" in (result.skipped_reason or "")


def test_run_letter_skips_without_style_guide(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _insert_job(db_conn, "job1")

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is False
    assert "style guide" in (result.skipped_reason or "")


def test_run_letter_generates_successfully(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")

    client = _fake_client([_ok_mapping_response()], [_ok_draft_response()])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is True
    assert result.markdown_path is not None
    assert result.markdown_path.exists()
    assert result.docx_path is not None
    assert result.docx_path.exists()
    assert result.spend_usd > 0
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Robotics Engineer" in markdown
    assert "I am a great fit" in markdown


def test_run_letter_retries_mapping_once_then_succeeds(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")

    error = anthropic.APIConnectionError(request=object())  # type: ignore[arg-type]
    client = _fake_client([error, _ok_mapping_response()], [_ok_draft_response()])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is True


def test_run_letter_fails_after_mapping_retry_exhausted(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")

    error = anthropic.APIConnectionError(request=object())  # type: ignore[arg-type]
    client = _fake_client([error, error], [])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is False
    assert "mapping failed" in (result.skipped_reason or "")


def test_run_letter_fails_after_draft_retry_exhausted(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")

    error = anthropic.APIConnectionError(request=object())  # type: ignore[arg-type]
    client = _fake_client([_ok_mapping_response()], [error, error])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is False
    assert "drafting failed" in (result.skipped_reason or "")


def test_run_letter_never_invents_uncovered_requirement(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The draft prompt must carry the NOT COVERED marker through, not silently drop it."""
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")

    captured: dict[str, Any] = {}

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured["content"] = kwargs["messages"][0]["content"]
            return super().create(**kwargs)

    client = SimpleNamespace()
    client.messages = CapturingMessages([_ok_mapping_response()], [_ok_draft_response()])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    generate_module.run_letter(db_conn, "job1")

    assert "NOT COVERED" in captured["content"]
    assert "PhD required" in captured["content"]


def _record_mapping_prompts(client: Any, seen: list[str]) -> None:
    original_parse = client.messages.parse

    def parse(**kwargs: Any) -> Any:
        seen.append(kwargs["messages"][0]["content"])
        return original_parse(**kwargs)

    client.messages.parse = parse


def test_looks_truncated() -> None:
    assert generate_module.looks_truncated("Start of an ad that gets cut off…")
    assert generate_module.looks_truncated("cut off...")
    assert not generate_module.looks_truncated("A complete ad.")
    assert not generate_module.looks_truncated(None)
    assert not generate_module.looks_truncated("")


def test_run_letter_flags_truncated_description_and_tells_the_model(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    db_conn.execute("UPDATE jobs SET description = ? WHERE job_id = 'job1'", ("Build ro…",))
    seen: list[str] = []
    client = _fake_client([_ok_mapping_response()], [_ok_draft_response()])
    _record_mapping_prompts(client, seen)
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is True
    assert result.jd_truncated is True
    assert "cut short by its source" in seen[0]


def test_run_letter_uses_jd_file_instead_of_stored_description(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    db_conn.execute("UPDATE jobs SET description = ? WHERE job_id = 'job1'", ("Build ro…",))
    jd = tmp_path / "full_ad.txt"
    jd.write_text("FULL AD: must know ROS2 and CAD.", encoding="utf-8")
    seen: list[str] = []
    client = _fake_client([_ok_mapping_response()], [_ok_draft_response()])
    _record_mapping_prompts(client, seen)
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1", jd_file=jd)

    assert result.generated is True
    assert result.jd_truncated is False
    assert "FULL AD: must know ROS2" in seen[0]
    assert "Build ro…" not in seen[0]


def test_run_letter_rejects_an_empty_jd_file(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    empty = tmp_path / "empty.txt"
    empty.write_text("   ", encoding="utf-8")

    result = generate_module.run_letter(db_conn, "job1", jd_file=empty)

    assert result.generated is False
    assert "job description" in (result.skipped_reason or "")


TAILORED_MAPPING = RequirementMapping(
    items=[
        RequirementEvidence(
            requirement="Fault-finding",
            jd_terms=["fault-finding", "root cause analysis"],
            evidence="Resolved thermal and vibration issues over 5 iterations",
            covered=True,
            reframe="Fault-finding thermal and vibration issues over 5 design iterations",
        ),
        RequirementEvidence(
            requirement="Reliability",
            jd_terms=["reliability"],
            evidence="Field-serviceable enclosure",
            covered=True,
            reframe="Designed for reliability in the field",
        ),
        RequirementEvidence(
            requirement="Maintenance planning tools",
            jd_terms=["maintenance planning tools"],
            covered=False,
        ),
    ],
    selection_criteria=["Communicates clearly", "Works collaboratively"],
    why_this_role=["Graduate rotations on fixed plant — matches hands-on interest"],
)


def _tailored_mapping_response() -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=TAILORED_MAPPING,
        usage=SimpleNamespace(input_tokens=500, output_tokens=200),
    )


def test_docx_metadata_and_filename(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    from docx import Document

    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(generate_module, "author_name", lambda: "Jane Doe")
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    client = _fake_client([_ok_mapping_response()], [_ok_draft_response()])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    year = datetime.now(UTC).year
    assert result.docx_path is not None
    assert result.docx_path.name == f"Cover Letter Acme {year}.docx"
    props = Document(str(result.docx_path)).core_properties
    assert props.author == "Jane Doe"
    assert props.last_modified_by == "Jane Doe"
    assert props.created is not None and props.created.year == year


def test_docx_path_reuses_own_name_and_never_overwrites_another_job(tmp_path: Path) -> None:
    first = generate_module.docx_path_for(tmp_path, "job1", "GHD", 2026)
    first.write_bytes(b"x")
    assert first.name == "Cover Letter GHD 2026.docx"
    assert generate_module.docx_path_for(tmp_path, "job1", "GHD", 2026) == first
    second = generate_module.docx_path_for(tmp_path, "job2", "GHD", 2026)
    assert second.name == "Cover Letter GHD 2026 (2).docx"


def test_letter_filename_strips_characters_windows_forbids() -> None:
    assert generate_module.letter_filename('A/B: "Co"?', 2026) == "Cover Letter A B Co 2026.docx"


def test_prompt_carries_ad_terms_reframes_criteria_and_why(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    captured: list[Any] = []

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return super().create(**kwargs)

    letter = "Fault-finding and reliability work, done honestly."
    client = SimpleNamespace()
    client.messages = CapturingMessages(
        [_tailored_mapping_response()], [_ok_draft_response(letter)]
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    content = captured[0]["messages"][0]["content"]
    assert "ad terms: fault-finding, root cause analysis" in content
    assert "in the ad's terms: Fault-finding thermal and vibration" in content
    assert "1. Communicates clearly" in content
    assert "Graduate rotations on fixed plant" in content
    assert "maintenance planning tools" in content and "NOT COVERED" in content
    assert "Do not reuse generic stock sentences" in captured[0]["system"]
    assert result.terms_used == ["fault-finding", "reliability"]
    assert result.revised is False  # nothing covered was missed, so no extra call
    assert result.selection_criteria == ["Communicates clearly", "Works collaboratively"]


def test_revises_once_when_covered_terms_are_missing(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    captured: list[Any] = []

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured.append(list(kwargs["messages"]))
            return super().create(**kwargs)

    generic = "I have a strong foundation in teamwork."
    tailored = "Root cause analysis of vibration issues, designed for reliability."
    client = SimpleNamespace()
    client.messages = CapturingMessages(
        [_tailored_mapping_response()],
        [_ok_draft_response(generic), _ok_draft_response(tailored)],
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert len(captured) == 2
    revision_request = captured[1][-1]["content"]
    assert "fault-finding, root cause analysis" in revision_request
    assert "maintenance planning tools" not in revision_request  # never push uncovered terms
    assert result.revised is True
    assert result.terms_missed == []
    assert result.markdown_path is not None
    assert tailored in result.markdown_path.read_text(encoding="utf-8")


def test_keeps_first_draft_if_revision_is_no_better(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    first, worse = "Generic letter one.", "Generic letter two."
    client = _fake_client(
        [_tailored_mapping_response()], [_ok_draft_response(first), _ok_draft_response(worse)]
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.revised is False
    assert result.markdown_path is not None
    assert first in result.markdown_path.read_text(encoding="utf-8")


def test_a_cut_off_draft_is_retried_not_saved(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    cut = _ok_draft_response("To the hiring manager, I iterated CAD mounts for a s", "max_tokens")
    whole = _ok_draft_response("To the hiring manager, a whole letter. Sincerely, Jo")
    client = _fake_client([_ok_mapping_response()], [cut, whole])
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.markdown_path is not None
    saved = result.markdown_path.read_text(encoding="utf-8")
    assert "a whole letter" in saved
    assert "for a s" not in saved


def test_stock_phrases_trigger_a_revision_that_removes_them(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(generate_module, "avoid_phrases", lambda: ["a strong foundation in"])
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    captured: list[Any] = []

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return super().create(**kwargs)

    stocky = "This gave me a strong foundation in teamwork."
    specific = "I mentored 80 members through CAD workshops."
    client = SimpleNamespace()
    client.messages = CapturingMessages(
        [_ok_mapping_response()], [_ok_draft_response(stocky), _ok_draft_response(specific)]
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert '- "a strong foundation in"' in captured[0]["system"]
    assert '"a strong foundation in"' in captured[1]["messages"][-1]["content"]
    assert result.revised is True
    assert result.markdown_path is not None
    assert specific in result.markdown_path.read_text(encoding="utf-8")


def test_factcheck_corrects_an_unsupported_claim(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    captured: list[Any] = []

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured.append(list(kwargs["messages"]))
            return super().create(**kwargs)

    invented = "I CNC-machined the mounts."
    honest = "I designed the mounts in CAD for printability."
    client = SimpleNamespace()
    client.messages = CapturingMessages(
        [_ok_mapping_response(), _factcheck_response(invented), _factcheck_response()],
        [_ok_draft_response(invented), _ok_draft_response(honest)],
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert invented in captured[1][-1]["content"]  # the fix request quotes the bad claim
    assert result.corrected_claims == 1
    assert result.unverified_claims == []
    assert result.markdown_path is not None
    saved = result.markdown_path.read_text(encoding="utf-8")
    assert honest in saved and invented not in saved


def test_factcheck_lists_claims_it_could_not_fix(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    claim = "Led a 200-person team."
    client = _fake_client(
        [_ok_mapping_response(), _factcheck_response(claim), _factcheck_response(claim)],
        [_ok_draft_response(claim), _ok_draft_response(claim)],
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.generated is True
    assert result.unverified_claims == [claim]


def test_portfolio_line_goes_before_the_thank_you_paragraph() -> None:
    letter = (
        "To the hiring manager,\n\nBody.\n\n"
        "Thank you for considering my application.\n\nSincerely,\nJo"
    )
    out = generate_module.ensure_portfolio_line(letter, "janedoe.dev")
    assert out == (
        "To the hiring manager,\n\nBody.\n\n"
        "If you'd like to see more of my work, you can find my portfolio at janedoe.dev.\n\n"
        "Thank you for considering my application.\n\nSincerely,\nJo"
    )


def test_portfolio_line_splits_a_paragraph_that_runs_into_the_thank_you() -> None:
    letter = "Body.\nThank you for considering my application.\nSincerely,\nJo"
    out = generate_module.ensure_portfolio_line(letter, "janedoe.dev")
    assert out.split("\n\n") == [
        "Body.",
        "If you'd like to see more of my work, you can find my portfolio at janedoe.dev.",
        "Thank you for considering my application.\nSincerely,\nJo",
    ]


def test_portfolio_line_not_duplicated_or_added_when_off() -> None:
    letter = "Body. See more at Janedoe.dev.\n\nThank you.\n\nSincerely,\nJo"
    assert generate_module.ensure_portfolio_line(letter, "janedoe.dev") == letter
    assert generate_module.ensure_portfolio_line("Body.\n\nThank you.", "") == "Body.\n\nThank you."


def test_letter_always_contains_portfolio_and_prompt_asks_for_it(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(generate_module, "portfolio_url", lambda: "janedoe.dev")
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    captured: list[Any] = []

    class CapturingMessages(FakeMessages):
        def create(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return super().create(**kwargs)

    client = SimpleNamespace()
    client.messages = CapturingMessages(
        [_ok_mapping_response()],
        [_ok_draft_response("To the hiring manager,\n\nBody.\n\nThank you.\n\nSincerely,\nJo")],
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert "written exactly as janedoe.dev" in captured[0]["system"]
    assert result.markdown_path is not None
    assert "my portfolio at janedoe.dev" in result.markdown_path.read_text(encoding="utf-8")


def _hyperlinks(docx_data: bytes) -> list[tuple[str, str]]:
    """(target url, shown text) for every hyperlink in a .docx."""
    import io

    from docx import Document
    from docx.oxml.ns import qn

    document = Document(io.BytesIO(docx_data))
    rels = document.part.rels
    links = []
    for paragraph in document.paragraphs:
        for link in paragraph._p.iter(qn("w:hyperlink")):
            shown = "".join(t.text or "" for t in link.iter(qn("w:t")))
            links.append((rels[link.get(qn("r:id"))].target_ref, shown))
    return links


def test_portfolio_display_strips_scheme_and_slash() -> None:
    assert generate_module.portfolio_display("https://janedoe.dev/") == "janedoe.dev"
    assert generate_module.portfolio_display("janedoe.dev") == "janedoe.dev"


def test_docx_makes_the_portfolio_a_clickable_link() -> None:
    import io

    from docx import Document

    text = (
        "To the hiring manager,\nI hope this finds you well.\n\n"
        "See my work at janedoe.dev, or https://www.janedoe.dev/.\n\n"
        "Sincerely,\nJo"
    )
    data = generate_module.docx_bytes(text, "Jo", "https://janedoe.dev/")

    assert _hyperlinks(data) == [
        ("https://janedoe.dev/", "janedoe.dev"),
        ("https://janedoe.dev/", "janedoe.dev"),
    ]
    paragraphs = [p.text for p in Document(io.BytesIO(data)).paragraphs]
    assert paragraphs[0] == "To the hiring manager,\nI hope this finds you well."
    assert paragraphs[1] == "See my work at janedoe.dev, or janedoe.dev."
    assert paragraphs[2] == "Sincerely,\nJo"


def test_docx_without_portfolio_has_no_links() -> None:
    assert _hyperlinks(generate_module.docx_bytes("Visit janedoe.dev.", "Jo")) == []


def test_generated_letter_links_the_portfolio(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "load_secrets", lambda: SECRETS_WITH_KEY)
    monkeypatch.setattr(generate_module, "portfolio_url", lambda: "https://janedoe.dev/")
    _setup_valid_inputs(tmp_path)
    _insert_job(db_conn, "job1")
    client = _fake_client(
        [_ok_mapping_response()],
        [_ok_draft_response("To the hiring manager,\n\nBody.\n\nThank you.\n\nSincerely,\nJo")],
    )
    monkeypatch.setattr(generate_module.anthropic, "Anthropic", lambda **kwargs: client)

    result = generate_module.run_letter(db_conn, "job1")

    assert result.markdown_path is not None and result.docx_path is not None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "my portfolio at janedoe.dev." in markdown
    assert "https://" not in markdown
    assert _hyperlinks(result.docx_path.read_bytes()) == [
        ("https://janedoe.dev/", "janedoe.dev")
    ]
