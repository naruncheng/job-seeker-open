from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from jobs.digest.discord import (
    MAX_DESCRIPTION_CHARS,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_MESSAGE_CHARS,
    _embed_char_count,
    _job_entry,
    _truncate,
    build_embeds,
    paginate_messages,
    send_discord_digest,
)
from jobs.digest.grouping import group_jobs


def _job(**overrides: object) -> dict:
    defaults = dict(
        job_id="abc123",
        title="Robotics Engineer",
        company="Acme",
        url="https://example.com/1",
        relevance=8,
        scope="local",
        industry="Robotics",
        role_type="Graduate Program",
        reason="Good fit",
        deadline=None,
        visa_flag=None,
        location_raw="Melbourne",
        source="greenhouse",
    )
    defaults.update(overrides)
    return defaults


def test_truncate_leaves_short_text_alone() -> None:
    assert _truncate("hello", 10) == "hello"


def test_truncate_shortens_long_text_with_ellipsis() -> None:
    result = _truncate("x" * 20, 10)
    assert len(result) == 10
    assert result.endswith("…")


def test_job_entry_shows_the_compact_fields_but_not_the_reason() -> None:
    job = _job(
        reason="SECRET-REASON",
        visa_flag="sponsorship offered",
        posted_at="2020-03-04T10:00:00Z",
        deadline="2020-04-05",
    )
    entry = _job_entry(job)
    assert "[Robotics Engineer](https://example.com/1)" in entry
    assert "8/10" in entry
    expected_parts = (
        "Acme",
        "Robotics",
        "Melbourne",
        "abc123",
        "Posted 4 Mar 2020",
        "Closes 5 Apr 2020",
    )
    for expected in expected_parts:
        assert expected in entry
    # the reason and visa note live in the email, not Discord
    for absent in ("SECRET-REASON", "sponsorship offered"):
        assert absent not in entry


def test_job_entry_omits_posted_and_closes_when_unknown() -> None:
    entry = _job_entry(_job(posted_at=None, deadline=None))
    assert "Posted" not in entry
    assert "Closes" not in entry


def test_job_entry_omits_location_when_unknown() -> None:
    entry = _job_entry(_job(location_raw=None))
    assert "None" not in entry
    assert "Acme · Robotics · `abc123`" in entry


def test_job_entry_keeps_markdown_link_intact() -> None:
    entry = _job_entry(_job(title="Eng [Grad]", url="https://example.com/a(b)"))
    assert "[Eng (Grad)](https://example.com/a(b%29)" in entry


def test_job_entry_truncates_long_text() -> None:
    entry = _job_entry(_job(title="x" * 500, company="c" * 500, location_raw="l" * 500))
    assert len(entry) < 400


def test_build_embeds_one_per_nonempty_scope() -> None:
    jobs = [_job(job_id="a", scope="local"), _job(job_id="b", scope="international")]
    grouped = group_jobs(jobs)
    embeds = build_embeds(grouped, {"greenhouse:acme": 2}, [])
    titles = [e["title"] for e in embeds]
    assert any(t.startswith("Local") for t in titles)
    assert any(t.startswith("International") for t in titles)
    assert "domestic" not in " ".join(titles).lower()


def test_build_embeds_splits_when_description_exceeds_budget() -> None:
    jobs = [_job(job_id=f"job{i}", title="T" * 90) for i in range(60)]
    embeds = build_embeds(group_jobs(jobs), {}, [])
    assert len(embeds) > 1
    assert embeds[0]["title"] == "Local (60)"
    assert all("(cont.)" in e["title"] for e in embeds[1:])
    assert all(len(e["description"]) <= MAX_DESCRIPTION_CHARS for e in embeds)


def test_build_embeds_attaches_footer_to_last_embed_only() -> None:
    jobs = [_job(job_id="a", scope="local"), _job(job_id="b", scope="international")]
    grouped = group_jobs(jobs)
    embeds = build_embeds(grouped, {"greenhouse:acme": 2}, ["jobspy"])
    assert "footer" not in embeds[0]
    assert "footer" in embeds[-1]
    assert "jobspy" in embeds[-1]["footer"]["text"]


def test_build_embeds_empty_grouping_returns_no_embeds() -> None:
    assert build_embeds(group_jobs([]), {}, []) == []


def test_footer_reports_new_listing_count() -> None:
    embeds = build_embeds(group_jobs([_job()]), {"greenhouse:acme": 216}, [], new_count=23)
    footer = embeds[-1]["footer"]["text"]
    assert "23 new listings" in footer
    assert "greenhouse:acme=216" in footer


def test_footer_omits_new_count_when_no_ingest_was_logged() -> None:
    footer = build_embeds(group_jobs([_job()]), {"adzuna": 5}, [], new_count=None)[-1]["footer"]
    assert "new listing" not in footer["text"]
    assert "adzuna=5" in footer["text"]


def test_footer_reports_zero_new_listings_rather_than_omitting() -> None:
    """0 is a real answer ('we looked, nothing new') and must stay
    distinguishable from None ('no ingest ran, we don't know')."""
    embeds = build_embeds(group_jobs([_job()]), {}, [], new_count=0)
    assert "0 new listings" in embeds[-1]["footer"]["text"]


def test_footer_uses_singular_for_one_new_listing() -> None:
    embeds = build_embeds(group_jobs([_job()]), {}, [], new_count=1)
    assert "1 new listing |" in embeds[-1]["footer"]["text"]


def test_footer_carries_new_count_cost_and_degraded_together() -> None:
    embeds = build_embeds(
        group_jobs([_job()]), {"adzuna": 5}, ["jobspy"], scoring_cost=0.0289, new_count=12
    )
    footer = embeds[-1]["footer"]["text"]
    assert "12 new listings" in footer
    assert "Scoring: $0.029" in footer
    assert "Degraded: jobspy" in footer


def test_nothing_new_message_still_reports_new_count(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=204)

    send_discord_digest("https://discord.example/webhook", group_jobs([]), {}, [], new_count=12)

    import json

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "12 new listings" in body["embeds"][0]["footer"]["text"]


def test_paginate_messages_splits_on_embed_count() -> None:
    embeds = [{"title": f"E{i}", "description": ""} for i in range(25)]
    messages = paginate_messages(embeds)
    assert len(messages) == 3  # 10 + 10 + 5
    assert all(len(m) <= 10 for m in messages)


def test_paginate_messages_splits_on_char_count() -> None:
    embeds = [{"title": f"E{i}", "description": "y" * 3500} for i in range(3)]
    messages = paginate_messages(embeds)
    assert len(messages) > 1


def test_send_discord_digest_nothing_new(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=204)
    ok = send_discord_digest("https://discord.example/webhook", group_jobs([]), {}, [])
    assert ok is True
    request = httpx_mock.get_requests()[0]
    import json

    body = json.loads(request.content)
    assert "Nothing new today" in body["embeds"][0]["title"]


def test_send_discord_digest_posts_grouped_jobs(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=204)
    jobs = [_job(job_id="a")]
    grouped = group_jobs(jobs)
    ok = send_discord_digest("https://discord.example/webhook", grouped, {}, [])
    assert ok is True


def test_send_discord_digest_retries_on_429_then_succeeds(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=429, headers={"retry-after": "0"})
    httpx_mock.add_response(status_code=204)
    jobs = [_job(job_id="a")]
    ok = send_discord_digest("https://discord.example/webhook", group_jobs(jobs), {}, [])
    assert ok is True


def test_send_discord_digest_returns_false_on_persistent_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=500)
    jobs = [_job(job_id="a")]
    ok = send_discord_digest("https://discord.example/webhook", group_jobs(jobs), {}, [])
    assert ok is False


@pytest.mark.parametrize("relevance,expected_color", [(10, 0x2ECC71), (7, 0x3498DB), (3, 0xF1C40F)])
def test_build_embeds_colour_by_score_band(relevance: int, expected_color: int) -> None:
    jobs = [_job(job_id="a", relevance=relevance)]
    grouped = group_jobs(jobs)
    embeds = build_embeds(grouped, {}, [])
    assert embeds[0]["color"] == expected_color


def test_long_digest_fits_discord_limits() -> None:
    """A big digest must be split so every embed and message stays under the limits."""
    jobs = [
        {
            "job_id": f"id{i:04d}",
            "title": "Graduate Mechatronics Engineer " + "x" * 80,
            "company": "Company " + "y" * 60,
            "relevance": 8,
            "reason": "r" * 400,
            "url": "https://example.com/" + "u" * 100,
            "industry": "Robotics",
            "role_type": "Graduate Program",
            "location_raw": "Melbourne VIC",
            "posted_at": "2020-03-04T10:00:00Z",
            "deadline": "2020-04-05",
        }
        for i in range(80)
    ]
    grouped = {"local": {"Robotics": {"Graduate Program": jobs}}}

    embeds = build_embeds(grouped, {"adzuna": 80}, [])  # type: ignore[arg-type]
    messages = paginate_messages(embeds)

    assert sum(e["description"].count("`id") for e in embeds) == 80
    for e in embeds:
        assert _embed_char_count(e) <= MAX_MESSAGE_CHARS
        assert len(e["description"]) <= MAX_DESCRIPTION_CHARS
    for m in messages:
        assert sum(_embed_char_count(e) for e in m) <= MAX_MESSAGE_CHARS
        assert len(m) <= MAX_EMBEDS_PER_MESSAGE
    assert embeds[0]["title"] == "Local (80)"
    assert all(e["title"].endswith("(cont.)") for e in embeds[1:])


def test_send_discord_digest_puts_the_date_heading_on_the_first_message_only(
    httpx_mock: HTTPXMock,
) -> None:
    import json

    jobs = [_job(job_id=f"job{i}", title="T" * 90) for i in range(60)]  # spills over messages
    grouped = group_jobs(jobs)
    n_messages = len(paginate_messages(build_embeds(grouped, {}, [])))
    assert n_messages >= 2
    for _ in range(n_messages):
        httpx_mock.add_response(status_code=204)

    date = "Monday 21 September 2026"
    ok = send_discord_digest("https://discord.example/webhook", grouped, {}, [], run_date=date)

    assert ok is True
    bodies = [json.loads(r.content) for r in httpx_mock.get_requests()]
    assert date in bodies[0]["content"]
    assert all("content" not in b for b in bodies[1:])


def test_send_discord_digest_nothing_new_also_shows_the_date(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(status_code=204)
    date = "Monday 21 September 2026"
    send_discord_digest("https://discord.example/webhook", group_jobs([]), {}, [], run_date=date)
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert date in body["content"]


def test_footer_shows_scoring_cost_beside_the_sources() -> None:
    embeds = build_embeds(
        group_jobs([_job()]), {"adzuna": 5, "jobspy:linkedin": 2}, ["jobspy"], scoring_cost=0.02893
    )
    footer = embeds[-1]["footer"]["text"]
    assert footer == "Sources: adzuna=5, jobspy:linkedin=2 | Scoring: $0.029 | Degraded: jobspy"


def test_footer_omits_scoring_cost_when_unknown() -> None:
    embeds = build_embeds(group_jobs([_job()]), {"adzuna": 5}, [])
    assert embeds[-1]["footer"]["text"] == "Sources: adzuna=5"


def test_nothing_new_message_also_shows_scoring_cost(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(status_code=204)
    send_discord_digest(
        "https://discord.example/webhook", group_jobs([]), {"adzuna": 1}, [], scoring_cost=0.0
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "Scoring: $0.000" in body["embeds"][0]["footer"]["text"]


def test_nothing_new_message_flags_degraded_stages(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=204)

    send_discord_digest(
        "https://discord.example/webhook", group_jobs([]), {}, ["scoring (API key rejected)"]
    )

    import json

    embed = json.loads(httpx_mock.get_requests()[0].content)["embeds"][0]
    assert "Nothing new today" not in embed["title"]
    assert "Degraded: scoring (API key rejected)" in embed["footer"]["text"]
