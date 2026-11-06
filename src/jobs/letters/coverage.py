"""Which of the ad's key terms made it into a letter.

Only requirements the resume actually covers count: a letter should use the
ad's words for those, and must not use them for the ones it doesn't cover.
Plain text matching, so it's free and deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from jobs.letters.schema import RequirementEvidence, RequirementMapping


@dataclass
class Coverage:
    used: list[str] = field(default_factory=list)
    missed: list[RequirementEvidence] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.used) + len(self.missed)

    def missed_terms(self) -> list[str]:
        return [item.jd_terms[0] for item in self.missed]


def _normalise(text: str) -> str:
    text = re.sub(r"[-‐–—/]", " ", text.lower())
    # Keeps "node.js"/"c++"/"c#" whole, but drops a sentence's full stop.
    tokens = (t.strip(".") for t in re.findall(r"[a-z0-9+#.]+", text))
    return " " + " ".join(t for t in tokens if t) + " "


def _term_in(term: str, normalised_letter: str) -> bool:
    words = _normalise(term).split()
    if not words:
        return False
    # Tolerate a plural/singular difference on the last word ("conveyor" vs "conveyors").
    last = words[-1].rstrip("s")
    stem = " ".join([*words[:-1], last])
    return re.search(rf" {re.escape(stem)}(s|es)? ", normalised_letter) is not None


ECHO_MIN_WORDS = 7


def _grams(text: str, n: int) -> set[tuple[str, ...]]:
    words = _normalise(text).split()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def echoed_from_ad(
    letter: str, ad: str, allowed: Sequence[str] = (), min_words: int = ECHO_MIN_WORDS
) -> list[str]:
    """Runs of `min_words`+ consecutive words copied from the ad, i.e. the ad's
    sentences echoed back rather than its key terms used. Shorter overlaps
    ("client satisfaction and quality of work") are the keyword matching we
    want, and so is quoting a selection criterion (pass those as `allowed`)."""
    ad_grams = _grams(ad, min_words)
    for text in allowed:
        ad_grams -= _grams(text, min_words)
    words = _normalise(letter).split()
    spans: list[str] = []
    i = 0
    while i <= len(words) - min_words:
        if tuple(words[i : i + min_words]) in ad_grams:
            end = i + min_words
            while end < len(words) and tuple(words[end - min_words + 1 : end + 1]) in ad_grams:
                end += 1
            spans.append(" ".join(words[i:end]))
            i = end
        else:
            i += 1
    return spans


def stock_phrases_in(letter: str, phrases: list[str]) -> list[str]:
    normalised = _normalise(letter)
    return [p for p in phrases if _normalise(p).strip() and _normalise(p) in normalised]


def term_coverage(letter: str, mapping: RequirementMapping) -> Coverage:
    normalised = _normalise(letter)
    coverage = Coverage()
    for item in mapping.items:
        if not item.covered or not item.jd_terms:
            continue
        hit = next((t for t in item.jd_terms if _term_in(t, normalised)), None)
        if hit:
            coverage.used.append(hit)
        else:
            coverage.missed.append(item)
    return coverage
