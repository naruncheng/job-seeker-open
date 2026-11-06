"""Human-friendly date formatting for the digest (Discord + email)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def format_date(value: str | None, today: date | None = None) -> str | None:
    """'2026-09-17T14:15:48Z' -> '17 Sep'; adds the year when it isn't this year.

    Returns None for missing or unparseable values so callers can just omit the
    field rather than show garbage.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None
    current_year = (today or date.today()).year
    text = f"{parsed.day} {parsed.strftime('%b')}"
    return text if parsed.year == current_year else f"{text} {parsed.year}"


DEFAULT_TIMEZONE = "Australia/Melbourne"


def run_date_label(now: datetime | None = None, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """The digest's date heading, e.g. 'Monday 21 September 2026'.

    Uses the reader's timezone, not the server's: the nightly run happens on a
    UTC machine, where 19:17 UTC is already tomorrow morning in Melbourne.
    Falls back to UTC if the timezone name is unknown.
    """
    moment = now or datetime.now(UTC)
    try:
        moment = moment.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        moment = moment.astimezone(UTC)
    return f"{moment.strftime('%A')} {moment.day} {moment.strftime('%B %Y')}"
