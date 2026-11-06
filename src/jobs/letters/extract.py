"""Plain-text extraction for the resume and past cover letters.

CLAUDE.md says "any format" for `inputs/cover_letters/`, so this supports
the formats people actually keep letters in: PDF, DOCX, and plain
text/markdown. An unrecognised extension is skipped with a warning, not a
crash — one bad file shouldn't block the style guide or a letter draft.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from pypdf import PdfReader

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def extract_text(path: Path) -> str | None:
    """Returns None (with a logged warning) if the file can't be read."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix == ".docx":
            return _extract_docx(path)
        if suffix in (".txt", ".md"):
            return path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("extract: failed to read %s", path)
        return None

    logger.warning("extract: unsupported file type %s, skipping", path)
    return None


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(path: Path) -> str:
    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def read_directory(directory: Path) -> dict[str, str]:
    """Reads every supported file in a directory. Returns {filename: text}."""
    texts: dict[str, str] = {}
    if not directory.is_dir():
        return texts
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            text = extract_text(path)
            if text and text.strip():
                texts[path.name] = text
    return texts
