from __future__ import annotations

from datetime import date

import pytest

from jobs.digest.dates import format_date

TODAY = date(2026, 9, 20)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-17T14:15:48Z", "17 Sep"),  # Adzuna style
        ("2026-09-18", "18 Sep"),  # JobSpy style
        ("2026-09-18T00:00:00+00:00", "18 Sep"),
        ("2026-10-05", "5 Oct"),
        ("2025-12-01", "1 Dec 2025"),  # other year -> year shown
        ("2027-01-15", "15 Jan 2027"),
    ],
)
def test_format_date(value: str, expected: str) -> None:
    assert format_date(value, today=TODAY) == expected


@pytest.mark.parametrize("value", [None, "", "not a date", "soon", "2026-13-45"])
def test_format_date_returns_none_for_missing_or_bad_values(value: str | None) -> None:
    assert format_date(value, today=TODAY) is None


def test_run_date_label_uses_melbourne_not_utc() -> None:
    from datetime import UTC, datetime

    from jobs.digest.dates import run_date_label

    # 19:17 UTC on 20 Sep is already 05:17 on Monday 21 Sep in Melbourne (AEST).
    now = datetime(2026, 9, 20, 19, 17, tzinfo=UTC)
    assert run_date_label(now) == "Monday 21 September 2026"


def test_run_date_label_respects_daylight_saving_and_custom_zone() -> None:
    from datetime import UTC, datetime

    from jobs.digest.dates import run_date_label

    assert run_date_label(datetime(2026, 12, 24, 19, 17, tzinfo=UTC)) == "Friday 25 December 2026"
    utc_moment = datetime(2026, 9, 20, 19, 17, tzinfo=UTC)
    assert run_date_label(utc_moment, "UTC") == "Sunday 20 September 2026"


def test_run_date_label_falls_back_to_utc_for_unknown_zone() -> None:
    from datetime import UTC, datetime

    from jobs.digest.dates import run_date_label

    now = datetime(2026, 9, 20, 19, 17, tzinfo=UTC)
    assert run_date_label(now, "Not/AZone") == "Sunday 20 September 2026"
