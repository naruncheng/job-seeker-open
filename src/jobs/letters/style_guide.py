"""Style guide extraction — phase A of cover letters, per CLAUDE.md §5.

Reads the resume + every file in inputs/cover_letters/ once and produces
data/style_guide.md: a specific, quote-backed description of how the
candidate writes, not generic advice. Generated once; after that it's meant
to be hand-tweaked, so a re-run without --force refuses to touch it, and
--force backs up the old version first (CLAUDE.md §3: never overwrite
anything in data/ without an explicit backup).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

from jobs.config import DATA_DIR, INPUTS_DIR, find_resume, load_secrets
from jobs.db import now_iso
from jobs.letters.extract import extract_text, read_directory
from jobs.score.budget import estimate_cost_usd

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
STYLE_GUIDE_PATH = DATA_DIR / "style_guide.md"
RESUME_PATH = INPUTS_DIR / "resume.pdf"
COVER_LETTERS_DIR = INPUTS_DIR / "cover_letters"

SYSTEM_PROMPT = """\
You analyse a person's writing style from their past cover letters and resume,
and produce a style guide another writer could follow to sound exactly like them.

Cover:
- Tone (formal/casual, warm/dry, confident/measured — be specific)
- Typical sentence length and rhythm
- How they open a letter (the actual pattern, not "with a greeting")
- How they close a letter
- Recurring phrasing or words they reach for
- What they tend to emphasise (skills vs. achievements vs. motivation, etc.)
- Structural pattern (paragraph order/purpose)
- Things they never do (clichés they avoid, structures they don't use)

Be specific and quote directly from the source letters to back up each
observation — not generic writing advice that could apply to anyone. Output
clean markdown with a heading per section."""


@dataclass
class StyleGuideResult:
    generated: bool = False
    skipped_reason: str | None = None
    letters_used: int = 0
    spend_usd: float = 0.0
    path: Path = STYLE_GUIDE_PATH


def _build_user_message(resume_text: str, letters: dict[str, str]) -> str:
    letters_block = "\n\n".join(
        f"--- {name} ---\n{text}" for name, text in letters.items()
    )
    return f"Resume:\n{resume_text}\n\nPast cover letters:\n\n{letters_block}"


def _backup_existing(path: Path) -> None:
    if not path.exists():
        return
    backup_path = path.with_name(f"{path.name}.bak.{now_iso().replace(':', '-')}")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info("style-guide: backed up existing guide to %s", backup_path)


def run_style_guide(force: bool = False) -> StyleGuideResult:
    secrets = load_secrets()
    if not secrets.anthropic_api_key:
        return StyleGuideResult(skipped_reason="missing ANTHROPIC_API_KEY")

    if STYLE_GUIDE_PATH.exists() and not force:
        return StyleGuideResult(
            skipped_reason=(
                f"{STYLE_GUIDE_PATH} already exists — pass --force to regenerate "
                "(the old version will be backed up first)"
            )
        )

    resume_path = find_resume(RESUME_PATH)
    if resume_path is None:
        return StyleGuideResult(
            skipped_reason=f"no resume found in {RESUME_PATH.parent} "
            "(expected resume.pdf, resume.docx, resume.txt or resume.md)"
        )
    resume_text = extract_text(resume_path)
    if not resume_text or not resume_text.strip():
        return StyleGuideResult(skipped_reason=f"could not extract text from {resume_path}")

    letters = read_directory(COVER_LETTERS_DIR)
    if not letters:
        return StyleGuideResult(
            skipped_reason=f"no past cover letters found in {COVER_LETTERS_DIR}"
        )

    client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(resume_text, letters)}],
    )
    guide_text = "".join(block.text for block in response.content if block.type == "text")
    cost = estimate_cost_usd(MODEL, response.usage.input_tokens, response.usage.output_tokens)

    if force:
        _backup_existing(STYLE_GUIDE_PATH)
    STYLE_GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STYLE_GUIDE_PATH.write_text(guide_text, encoding="utf-8")

    logger.info(
        "style-guide: generated from %d letter(s), spend=$%.4f", len(letters), cost
    )
    return StyleGuideResult(generated=True, letters_used=len(letters), spend_usd=cost)
