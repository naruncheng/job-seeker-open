from __future__ import annotations

from pathlib import Path

from jobs.config import find_resume


def test_finds_docx_when_no_pdf(tmp_path: Path) -> None:
    (tmp_path / "resume.docx").write_bytes(b"x")
    assert find_resume(tmp_path / "resume.pdf") == tmp_path / "resume.docx"


def test_prefers_pdf_when_several_exist(tmp_path: Path) -> None:
    (tmp_path / "resume.docx").write_bytes(b"x")
    (tmp_path / "resume.pdf").write_bytes(b"x")
    assert find_resume(tmp_path / "resume.pdf") == tmp_path / "resume.pdf"


def test_returns_none_when_no_resume(tmp_path: Path) -> None:
    assert find_resume(tmp_path / "resume.pdf") is None
