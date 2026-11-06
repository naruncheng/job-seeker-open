from __future__ import annotations

import pytest

from jobs.normalize.location import classify_scope, normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Melbourne VIC", "local"),
        ("Melbourne, Australia", "local"),
        ("Sydney NSW", "domestic"),
        ("Brisbane, Queensland", "domestic"),
        ("Remote (Australia)", "domestic"),
        ("San Francisco, CA", "international"),
        ("London, UK", "international"),
        (None, "domestic"),
        ("", "domestic"),
    ],
)
def test_classify_scope(raw: str | None, expected: str) -> None:
    assert classify_scope(raw, local_city="Melbourne", local_country="Australia") == expected


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert normalize_text("  Melbourne   VIC  ") == "melbourne vic"
    assert normalize_text(None) == ""
