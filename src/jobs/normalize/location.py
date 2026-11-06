"""Location parsing: classify a raw location string as local/domestic/international.

Kept deliberately simple for increment 1 — string matching against the
configured local city/country, not a geocoding service. Good enough to
group a digest; revisit if it misclassifies real listings often.
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")

AU_STATE_HINTS = {
    "nsw",
    "vic",
    "qld",
    "wa",
    "sa",
    "tas",
    "act",
    "nt",
    "new south wales",
    "victoria",
    "queensland",
    "western australia",
    "south australia",
    "tasmania",
    "australian capital territory",
    "northern territory",
}


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def normalize_text(value: str | None) -> str:
    """Lowercase, collapse whitespace, strip — used as a dedupe key input."""
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip().lower()


def classify_scope(
    location_raw: str | None,
    local_city: str,
    local_country: str = "Australia",
) -> str:
    """Return 'local', 'domestic', or 'international'."""
    text = normalize_text(location_raw)
    if not text:
        return "domestic"

    if normalize_text(local_city) in text:
        return "local"

    if normalize_text(local_country) in text or "australia" in text:
        return "domestic"

    if any(_contains_word(text, hint) for hint in AU_STATE_HINTS):
        return "domestic"

    return "international"
