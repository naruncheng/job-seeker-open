from __future__ import annotations

from pathlib import Path

from docx import Document
from pypdf import PdfWriter

from jobs.letters.extract import extract_text, read_directory


def _write_minimal_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        writer.write(fh)


def _write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(str(path))


def test_extract_text_reads_txt(tmp_path: Path) -> None:
    path = tmp_path / "letter.txt"
    path.write_text("Dear hiring manager,\nI am excited.", encoding="utf-8")
    assert extract_text(path) == "Dear hiring manager,\nI am excited."


def test_extract_text_reads_md(tmp_path: Path) -> None:
    path = tmp_path / "letter.md"
    path.write_text("# Cover letter\nBody text.", encoding="utf-8")
    assert "Body text." in (extract_text(path) or "")


def test_extract_text_reads_docx(tmp_path: Path) -> None:
    path = tmp_path / "letter.docx"
    _write_docx(path, "I am a strong candidate for this role.")
    result = extract_text(path)
    assert result is not None
    assert "strong candidate" in result


def test_extract_text_reads_pdf_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "resume.pdf"
    _write_minimal_pdf(path)
    result = extract_text(path)
    assert result is not None  # blank page -> empty string, not None/crash
    assert isinstance(result, str)


def test_extract_text_returns_none_for_unsupported_type(tmp_path: Path) -> None:
    path = tmp_path / "resume.rtf"
    path.write_text("some rtf content", encoding="utf-8")
    assert extract_text(path) is None


def test_extract_text_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a real pdf")
    assert extract_text(path) is None


def test_read_directory_collects_supported_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "b.md").write_text("markdown body", encoding="utf-8")
    (tmp_path / "c.rtf").write_text("skip me", encoding="utf-8")

    result = read_directory(tmp_path)

    assert set(result.keys()) == {"a.txt", "b.md"}
    assert result["a.txt"] == "hello world"


def test_read_directory_skips_empty_files(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("   ", encoding="utf-8")
    (tmp_path / "real.txt").write_text("content", encoding="utf-8")

    result = read_directory(tmp_path)

    assert "empty.txt" not in result
    assert "real.txt" in result


def test_read_directory_missing_dir_returns_empty() -> None:
    assert read_directory(Path("/nonexistent/does/not/exist")) == {}
