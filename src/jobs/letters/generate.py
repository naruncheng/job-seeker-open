"""Per-letter generation — phase B of cover letters, per CLAUDE.md §5.

Fetches the JD (already stored from ingest) and best-effort company
context, selects similar past letters, forces an explicit
requirement→evidence grounding step, then drafts in the candidate's voice.
Never invents experience, employers, dates, or numbers — the grounding step
exists specifically so every claim in the draft traces to the mapping.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from pydantic import ValidationError

from jobs.config import OUTPUTS_DIR, find_resume, load_secrets, load_settings
from jobs.db import get_job
from jobs.letters.company_context import fetch_company_context
from jobs.letters.coverage import echoed_from_ad, stock_phrases_in, term_coverage
from jobs.letters.extract import extract_text, read_directory
from jobs.letters.schema import FactCheck, RequirementMapping
from jobs.letters.similarity import most_similar
from jobs.letters.style_guide import COVER_LETTERS_DIR, RESUME_PATH, STYLE_GUIDE_PATH
from jobs.score.budget import estimate_cost_usd

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
# Generous because Sonnet's thinking tokens count against these caps too; a
# 2048 cap once cut a revised letter off mid-sentence.
MAPPING_MAX_TOKENS = 8192
DRAFT_MAX_TOKENS = 8192
LETTERS_DIR = OUTPUTS_DIR / "letters"

MAPPING_SYSTEM = """\
You map a job description's requirements to evidence on a candidate's resume,
before any cover letter is drafted.

For each distinct requirement or preference in the job description (duties
the role involves count too — "you will assist with root cause analysis"
implies the skill):
- jd_terms: 1-3 of the ad's own key words for it, copied exactly as the ad
  writes them, each at most 4 words — the terms an ATS or screener would
  search for: tools, methods, equipment, domains, named duties, named soft
  skills ("root cause analysis", "conveyors", "stakeholder relationships").
  Never a whole sentence or duty description, and for qualifications use the
  field ("Mechanical Engineering"), not wording like "tertiary qualifications".
- evidence: quote or closely paraphrase the resume if it provides real
  evidence. If it doesn't, say so — never infer or invent evidence.
- covered: true only if the evidence is the same kind of thing the ad means
  in its own field. Matching words with a different meaning don't count: at
  a pipeline engineering firm "equipment sizing and selection" means pumps,
  vessels and piping, so choosing sensors for a robot is NOT COVERED there
  (it can still be mentioned as related experience, not as that skill).
- reframe (covered items only): one sentence describing that same evidence
  in the ad's vocabulary. Renaming is allowed when it is accurate (iterating
  a design to fix thermal and vibration problems IS fault-finding); changing
  scope, adding tools or methods not on the resume, or adding outcomes or
  numbers is not.

selection_criteria: fill only when the ad explicitly asks applicants to
address selection criteria (common in Australian government roles); list
each one as worded, in order.

why_this_role: 1-3 specifics about this employer or program (projects,
sectors, focus areas, program features) from the ad or company context that
genuinely connect to the candidate's background or stated interests."""

DRAFT_SYSTEM_PREFIX = """\
You draft a cover letter in the candidate's own voice and tailor it closely
to one job.

Voice: follow the style guide below for tone, sentence rhythm, paragraph
length, overall structure and length (one page, about 350-450 words). That
is all the style guide controls.

The only template text to keep word for word is the greeting (the
salutation and the goodwill line after it) and the sign-off (the final
thank-you sentences and "Sincerely," with the name). Everything between
them is written fresh for this job.

Content — these rules override the style guide's "recurring phrasing":
- Do not reuse generic stock sentences from the example letters or the
  style guide. In particular, never use these (or close variants):
{avoid}
  Don't end the body with a summary of general strengths; go straight from
  the last specific paragraph to the thank-you. The example letters show
  voice only.
- Use the ad's own terms. For each covered requirement, describe the
  evidence using its reframe and jd_terms, so the letter contains the ad's
  key words naturally. Prioritise the requirements the ad stresses most.
  Never list keywords or stuff them in; every term must sit in a sentence
  about something the candidate actually did. Use the ad's words, not its
  sentences: never echo the ad's phrasing back ("interact with inter
  discipline engineers to execute multidiscipline projects"), and never use
  a term to make the evidence sound bigger than it is ("a core part of my
  work", "applied analytical capability").
- Mention each piece of background once; don't restate the degree.
- Prefer concrete specifics and the resume's real numbers (iterations, team
  sizes, frequencies) over general claims, even where the style guide says
  the candidate rarely quantifies.
- Include one or two sentences on why this role or employer, drawn from
  why_this_role, naming the specific thing.
- If selection criteria are listed, address every one clearly and in order,
  in prose, using each criterion's wording so a panel can tick it off.

Hard rule: every claim must trace to the requirement→evidence mapping. Never
invent experience, employers, tools, dates, outcomes or numbers. Where a
requirement is NOT COVERED, don't claim it — omit it, or name it honestly as
something the candidate is keen to develop.
{portfolio}
Output only the letter itself (greeting to sign-off), no title or commentary.

Style guide:
"""

REVISION_REQUEST = """\
Please revise this draft.
{problems}
Keep the voice, greeting, sign-off, length (one page), and the rule that
every claim traces to the mapping. Don't add new claims. Output only the
revised letter."""

REVISION_MISSING_TERMS = """
It leaves out the ad's own wording for some requirements the candidate does
meet:
{missing}
Work in the ones that fit a sentence about that real evidence and read
naturally; skip any that would sound forced. Never bolt a term onto the end
of a sentence ("…which reflects X") or restate the ad's phrasing awkwardly.
"""

REVISION_WORDING = """
These phrases are either generic stock lines or copied word for word from
the ad: {phrases}. Rewrite each in the candidate's own words about their own
evidence (keeping any short key term the ad uses), or cut it.
"""

FACTCHECK_SYSTEM = """\
You check a cover letter against the candidate's resume before it is sent.
List every statement about the candidate that the resume does not support:
- experience, tools, methods, numbers, dates or outcomes the resume doesn't
  mention;
- a skill or method attached to a project the resume doesn't connect it to
  (e.g. a tool from the skills list described as used on a specific project);
- a detail that was changed or made bigger (different requirements,
  "recurring" failures, larger scope, a different role or team size).
Fine, don't list: interest or motivation, the portfolio link, the employer's details taken from
the ad, and accurate rewording of resume content in the ad's vocabulary
("resolved thermal and vibration issues" → "fault-finding").
Quote the letter's exact text for each. Return an empty list if everything
checks out."""

FACTCHECK_FIX = """\
A check against the resume found statements it doesn't support:
{claims}
Correct each so it matches the resume exactly, or remove it. Change nothing
else. Output only the corrected letter."""

REVISE_WHEN_MISSED = 2

DEFAULT_AVOID_PHRASES = [
    "a strong foundation in",
    "I'm confident in my ability to adapt quickly",
    "a leading company like",
    "practical industry experience",
]

T = TypeVar("T")


class ParsedOutputMissing(RuntimeError):
    """Raised when the API returns no parsed structured output to retry on."""


class DraftCutOff(RuntimeError):
    """Raised when a draft hit the token cap, so it ends mid-sentence."""


@dataclass
class LetterResult:
    generated: bool = False
    skipped_reason: str | None = None
    markdown_path: Path | None = None
    docx_path: Path | None = None
    spend_usd: float = 0.0
    jd_truncated: bool = False
    terms_used: list[str] = field(default_factory=list)
    terms_missed: list[str] = field(default_factory=list)
    revised: bool = False
    selection_criteria: list[str] = field(default_factory=list)
    corrected_claims: int = 0
    unverified_claims: list[str] = field(default_factory=list)

    def coverage_summary(self) -> str | None:
        total = len(self.terms_used) + len(self.terms_missed)
        if not total:
            return None
        text = f"Uses the ad's wording for {len(self.terms_used)}/{total} requirements you meet"
        if self.terms_missed:
            text += f" (not worked in: {', '.join(self.terms_missed)})"
        if self.selection_criteria:
            text += f". Addresses {len(self.selection_criteria)} selection criteria"
        return text + "."


def author_name() -> str:
    return str(load_settings().get("letters", "author_name", default="") or "")


PORTFOLIO_INSTRUCTION = """
Just before the closing thank-you, include one short sentence in the
candidate's voice inviting the reader to see more of their work on their
portfolio website, written exactly as {url}.
"""


def portfolio_url() -> str:
    return str(load_settings().get("letters", "portfolio_url", default="") or "").strip()


def portfolio_display(url: str) -> str:
    """'https://janedoe.dev/' -> 'janedoe.dev': what the letter shows."""
    return re.sub(r"^https?://", "", url, flags=re.IGNORECASE).rstrip("/")


def _link_pattern(url: str) -> re.Pattern[str]:
    """Matches the portfolio however the letter writes it, with or without
    https://, www. or a trailing slash, so all of it becomes one link."""
    host = re.escape(portfolio_display(url).removeprefix("www."))
    return re.compile(rf"(?:https?://)?(?:www\.)?{host}/?", re.IGNORECASE)


def _add_hyperlink(paragraph: Paragraph, url: str, text: str) -> None:
    """python-docx has no hyperlink API, so this writes the w:hyperlink XML
    directly, styled the way Word styles a typed link (blue, underlined)."""
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    run.append(props)
    text_el = OxmlElement("w:t")
    text_el.text = text
    text_el.set(qn("xml:space"), "preserve")
    run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def ensure_portfolio_line(letter: str, url: str) -> str:
    """Guarantees the portfolio link is in the letter: if the draft left it
    out, adds a standard sentence as its own paragraph before the thank-you
    (or before the sign-off if there's no thank-you)."""
    if not url or url.lower() in letter.lower():
        return letter
    line = f"If you'd like to see more of my work, you can find my portfolio at {url}."
    paragraphs = letter.split("\n\n")
    for i, paragraph in enumerate(paragraphs):
        if paragraph.lstrip().lower().startswith(("thank you", "sincerely")):
            return "\n\n".join([*paragraphs[:i], line, *paragraphs[i:]])
        lines = paragraph.split("\n")
        for j, text in enumerate(lines):
            if j and text.lstrip().lower().startswith(("thank you", "sincerely")):
                head, tail = "\n".join(lines[:j]), "\n".join(lines[j:])
                return "\n\n".join([*paragraphs[:i], head, line, tail, *paragraphs[i + 1 :]])
    return f"{letter.rstrip()}\n\n{line}"


def avoid_phrases() -> list[str]:
    phrases = load_settings().get("letters", "avoid_phrases", default=None)
    if not isinstance(phrases, list):
        return DEFAULT_AVOID_PHRASES
    return [str(p) for p in phrases if str(p).strip()]


TRUNCATION_NOTE = (
    "\n\n[NOTE: this job description was cut short by its source. Do not assume or "
    "invent requirements that are not shown above.]"
)


def looks_truncated(description: str | None) -> bool:
    """Sources like Adzuna return only the first ~500 chars, ending in an ellipsis."""
    return bool(description) and (description or "").rstrip().endswith(("…", "..."))


def _with_retry(call: Callable[[], T]) -> T | None:
    for attempt in (1, 2):
        try:
            return call()
        except (anthropic.APIError, ValidationError, ParsedOutputMissing, DraftCutOff) as exc:
            logger.warning("letters: attempt %d failed: %s", attempt, exc)
    logger.error("letters: gave up after retry")
    return None


def _build_mapping_message(resume_text: str, jd_text: str, company_context: str | None) -> str:
    message = f"Resume:\n{resume_text}\n\nJob description:\n{jd_text}"
    if company_context:
        message += f"\n\nCompany context (from their website):\n{company_context}"
    return message


def _format_mapping(mapping: RequirementMapping) -> str:
    lines = []
    for item in mapping.items:
        terms = f" [ad terms: {', '.join(item.jd_terms)}]" if item.jd_terms else ""
        if item.covered:
            lines.append(
                f"- {item.requirement}{terms}: COVERED — evidence: {item.evidence}"
                + (f" | in the ad's terms: {item.reframe}" if item.reframe else "")
            )
        else:
            lines.append(f"- {item.requirement}{terms}: NOT COVERED")
    text = "\n".join(lines)
    if mapping.selection_criteria:
        criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(mapping.selection_criteria, 1))
        text += f"\n\nSelection criteria the ad asks applicants to address:\n{criteria}"
    if mapping.why_this_role:
        text += "\n\nWhy this role (specifics to draw on):\n" + "\n".join(
            f"- {w}" for w in mapping.why_this_role
        )
    return text


def _build_draft_message(
    resume_text: str,
    jd_text: str,
    company_context: str | None,
    example_letters: dict[str, str],
    mapping: RequirementMapping,
) -> str:
    examples_block = "\n\n".join(
        f"--- example: {name} ---\n{text}" for name, text in example_letters.items()
    )
    parts = [
        f"Resume:\n{resume_text}",
        f"Job description:\n{jd_text}",
    ]
    if company_context:
        parts.append(f"Company context (from their website):\n{company_context}")
    parts.append(f"Requirement -> evidence mapping:\n{_format_mapping(mapping)}")
    if examples_block:
        parts.append(f"Similar past letters (for voice reference only):\n\n{examples_block}")
    return "\n\n".join(parts)


def _build_docx(text: str, author: str, link_url: str = "") -> DocxDocument:
    document = Document()
    pattern = _link_pattern(link_url) if link_url else None
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        paragraph = document.add_paragraph()
        position = 0
        for match in pattern.finditer(block) if pattern else ():
            paragraph.add_run(block[position : match.start()])
            _add_hyperlink(paragraph, link_url, portfolio_display(link_url))
            position = match.end()
        paragraph.add_run(block[position:])
    # python-docx's template otherwise stamps author "python-docx" and a 2013 date.
    props = document.core_properties
    now = datetime.now(UTC).replace(microsecond=0)
    props.author = author
    props.last_modified_by = author
    props.title = "Cover Letter"
    props.created = now
    props.modified = now
    props.revision = 1
    return document


def _write_docx(text: str, path: Path, author: str, link_url: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _build_docx(text, author, link_url).save(str(path))


def docx_bytes(text: str, author: str, link_url: str = "") -> bytes:
    buffer = io.BytesIO()
    _build_docx(text, author, link_url).save(buffer)
    return buffer.getvalue()


_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_NAMES_INDEX = ".docx_names.json"


def letter_filename(company: str, year: int) -> str:
    """'Cover Letter Whitehaven Coal 2026.docx' — the user's own naming scheme."""
    safe = " ".join(_UNSAFE_FILENAME_RE.sub(" ", company).split()).rstrip(". ") or "Company"
    return f"Cover Letter {safe} {year}.docx"


def docx_path_for(letters_dir: Path, job_id: str, company: str, year: int) -> Path:
    """Reuses this job's previous filename (so regenerating overwrites its own
    letter); otherwise picks a free one, adding ' (2)' etc. rather than
    overwriting another job's letter to the same company."""
    index_path = letters_dir / _NAMES_INDEX
    try:
        index: dict[str, str] = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    if job_id in index:
        return letters_dir / index[job_id]

    base = letter_filename(company, year)
    taken = set(index.values())
    name, n = base, 2
    while name in taken or (letters_dir / name).exists():
        name = base.replace(".docx", f" ({n}).docx")
        n += 1
    index[job_id] = name
    letters_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return letters_dir / name


def run_letter(conn: sqlite3.Connection, job_id: str, jd_file: Path | None = None) -> LetterResult:
    """`jd_file`: the full job ad saved as pdf/docx/txt/md, used instead of the
    (possibly truncated) description stored at ingest."""
    secrets = load_secrets()
    if not secrets.anthropic_api_key:
        return LetterResult(skipped_reason="missing ANTHROPIC_API_KEY")

    job = get_job(conn, job_id)
    if job is None:
        return LetterResult(skipped_reason=f"no job found with id {job_id!r}")

    if not STYLE_GUIDE_PATH.exists():
        return LetterResult(
            skipped_reason=f"no style guide at {STYLE_GUIDE_PATH} — run `jobs style-guide` first"
        )
    style_guide = STYLE_GUIDE_PATH.read_text(encoding="utf-8")

    resume_path = find_resume(RESUME_PATH)
    if resume_path is None:
        return LetterResult(
            skipped_reason=f"no resume found in {RESUME_PATH.parent} "
            "(expected resume.pdf, resume.docx, resume.txt or resume.md)"
        )
    resume_text = extract_text(resume_path)
    if not resume_text or not resume_text.strip():
        return LetterResult(skipped_reason=f"could not extract text from {resume_path}")

    letters = read_directory(COVER_LETTERS_DIR)
    if not letters:
        return LetterResult(skipped_reason=f"no past cover letters found in {COVER_LETTERS_DIR}")

    jd_truncated = False
    if jd_file is not None:
        jd_text = extract_text(jd_file) or ""
        if not jd_text.strip():
            return LetterResult(skipped_reason=f"could not read a job description from {jd_file}")
    else:
        jd_text = job["description"] or job["title"]
        jd_truncated = looks_truncated(job["description"])
        if jd_truncated:
            logger.warning(
                "letters: the stored job description is truncated (%d chars); pass "
                "--jd-file with the full ad for a better letter",
                len(jd_text),
            )
            jd_text += TRUNCATION_NOTE
    company_context = fetch_company_context(job["url"])
    similar_names = most_similar(jd_text, letters, top_n=3)
    example_letters = {name: letters[name] for name in similar_names}

    client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)
    spend = 0.0

    def record(call: str, usage: Any) -> None:
        nonlocal spend
        cost = estimate_cost_usd(MODEL, usage.input_tokens, usage.output_tokens)
        spend += cost
        logger.info(
            "letters: %s call: %d in, %d out tokens, $%.4f",
            call,
            usage.input_tokens,
            usage.output_tokens,
            cost,
        )

    def call_mapping() -> RequirementMapping:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAPPING_MAX_TOKENS,
            system=MAPPING_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": _build_mapping_message(resume_text, jd_text, company_context),
                }
            ],
            output_format=RequirementMapping,
        )
        record("mapping", response.usage)
        if response.parsed_output is None:
            raise ParsedOutputMissing("no parsed_output on requirement mapping response")
        return response.parsed_output

    mapping = _with_retry(call_mapping)
    if mapping is None:
        return LetterResult(
            skipped_reason="requirement->evidence mapping failed after retry", spend_usd=spend
        )

    draft_messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": _build_draft_message(
                resume_text, jd_text, company_context, example_letters, mapping
            ),
        }
    ]

    avoid = avoid_phrases()
    portfolio = portfolio_url()
    shown = portfolio_display(portfolio)
    system = (
        DRAFT_SYSTEM_PREFIX.replace("{avoid}", "\n".join(f'  - "{p}"' for p in avoid)).replace(
            "{portfolio}", PORTFOLIO_INSTRUCTION.format(url=shown) if portfolio else ""
        )
        + style_guide
    )

    def call_draft() -> str:
        response = client.messages.create(
            model=MODEL,
            max_tokens=DRAFT_MAX_TOKENS,
            system=system,
            messages=draft_messages,
        )
        record("draft", response.usage)
        if response.stop_reason == "max_tokens":
            raise DraftCutOff("draft hit the output token cap")
        return "".join(block.text for block in response.content if block.type == "text")

    letter_text = _with_retry(call_draft)
    if not letter_text:
        return LetterResult(skipped_reason="letter drafting failed after retry", spend_usd=spend)

    raw_ad = jd_text.removesuffix(TRUNCATION_NOTE)

    def wording_problems(text: str) -> list[str]:
        """Stock phrases plus sentences echoed from the ad: both should be zero."""
        return stock_phrases_in(text, avoid) + echoed_from_ad(
            text, raw_ad, allowed=mapping.selection_criteria
        )

    coverage = term_coverage(letter_text, mapping)
    wording = wording_problems(letter_text)
    revised = False
    if len(coverage.missed) >= REVISE_WHEN_MISSED or wording:
        problems = ""
        if len(coverage.missed) >= REVISE_WHEN_MISSED:
            problems += REVISION_MISSING_TERMS.format(
                missing="\n".join(
                    f"- {item.requirement} — ad terms: {', '.join(item.jd_terms)}; "
                    f"evidence: {item.reframe or item.evidence}"
                    for item in coverage.missed
                )
            )
        if wording:
            problems += REVISION_WORDING.format(phrases="; ".join(f'"{p}"' for p in wording))
        draft_messages += [
            {"role": "assistant", "content": letter_text},
            {"role": "user", "content": REVISION_REQUEST.format(problems=problems)},
        ]
        revision = _with_retry(call_draft)
        if revision:
            revised_coverage = term_coverage(revision, mapping)
            revised_wording = wording_problems(revision)
            used, revised_used = len(coverage.used), len(revised_coverage.used)
            if (len(revised_wording) < len(wording) and revised_used >= used) or (
                revised_used > used and len(revised_wording) <= len(wording)
            ):
                letter_text, coverage, wording, revised = (
                    revision,
                    revised_coverage,
                    revised_wording,
                    True,
                )
    if wording:
        logger.warning("letters: draft still has generic/copied wording: %s", "; ".join(wording))

    def call_factcheck(text: str) -> FactCheck:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAPPING_MAX_TOKENS,
            system=FACTCHECK_SYSTEM,
            messages=[{"role": "user", "content": f"Resume:\n{resume_text}\n\nLetter:\n{text}"}],
            output_format=FactCheck,
        )
        record("fact-check", response.usage)
        if response.parsed_output is None:
            raise ParsedOutputMissing("no parsed_output on fact-check response")
        return response.parsed_output

    # Last, so no later rewrite can reintroduce what it corrects.
    corrected = 0
    unverified: list[str] = []
    check = _with_retry(partial(call_factcheck, letter_text))
    if check is None:
        unverified = ["(the automatic fact-check failed — read every claim against your resume)"]
    elif check.unsupported:
        claims = "\n".join(f'- "{c.quote}" — {c.problem}' for c in check.unsupported)
        draft_messages += [
            {"role": "assistant", "content": letter_text},
            {"role": "user", "content": FACTCHECK_FIX.format(claims=claims)},
        ]
        fixed = _with_retry(call_draft)
        if not fixed:
            unverified = [c.quote for c in check.unsupported]
        else:
            letter_text = fixed
            recheck = _with_retry(partial(call_factcheck, letter_text))
            if recheck is None:
                unverified = ["(the re-check after corrections failed — read every claim)"]
            else:
                corrected = max(len(check.unsupported) - len(recheck.unsupported), 0)
                unverified = [c.quote for c in recheck.unsupported]
        coverage = term_coverage(letter_text, mapping)
    if unverified:
        logger.warning("letters: claims to check by hand: %s", " | ".join(unverified))

    letter_text = ensure_portfolio_line(letter_text, shown)

    author = author_name()
    markdown_path = LETTERS_DIR / f"{job_id}.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        f"# {job['title']} — {job['company']}\n\n{letter_text}\n", encoding="utf-8"
    )
    docx_path = docx_path_for(LETTERS_DIR, job_id, job["company"], datetime.now(UTC).year)
    _write_docx(letter_text, docx_path, author, portfolio)

    logger.info(
        "letters: generated for job %s, %d/%d ad terms used%s, spend=$%.4f",
        job_id,
        len(coverage.used),
        coverage.total,
        " (after one revision)" if revised else "",
        spend,
    )
    return LetterResult(
        generated=True,
        markdown_path=markdown_path,
        docx_path=docx_path,
        spend_usd=spend,
        jd_truncated=jd_truncated,
        terms_used=coverage.used,
        terms_missed=coverage.missed_terms(),
        revised=revised,
        selection_criteria=mapping.selection_criteria,
        corrected_claims=corrected,
        unverified_claims=unverified,
    )
