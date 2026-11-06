from __future__ import annotations

from jobs.letters.similarity import most_similar


def test_most_similar_ranks_closer_text_first() -> None:
    candidates = {
        "robotics.md": "robotics automation mechatronics embedded firmware engineering",
        "finance.md": "finance accounting spreadsheets audits budgets reconciliation",
    }
    result = most_similar("robotics automation engineer embedded systems role", candidates, top_n=2)
    assert result[0] == "robotics.md"


def test_most_similar_respects_top_n() -> None:
    candidates = {f"letter{i}.md": f"content number {i} about engineering" for i in range(5)}
    result = most_similar("engineering role", candidates, top_n=2)
    assert len(result) == 2


def test_most_similar_returns_fewer_when_not_enough_candidates() -> None:
    candidates = {"only.md": "some text here"}
    result = most_similar("query text", candidates, top_n=3)
    assert result == ["only.md"]


def test_most_similar_empty_candidates_returns_empty() -> None:
    assert most_similar("query", {}, top_n=3) == []


def test_most_similar_handles_no_shared_vocabulary() -> None:
    candidates = {"a.md": "zzz yyy xxx", "b.md": "qqq www"}
    result = most_similar("completely different words entirely", candidates, top_n=2)
    assert set(result) == {"a.md", "b.md"}


def test_most_similar_identical_text_scores_highest() -> None:
    candidates = {
        "exact.md": "robotics automation engineer embedded systems",
        "unrelated.md": "sales pipeline crm customer relationship",
    }
    result = most_similar("robotics automation engineer embedded systems", candidates, top_n=1)
    assert result == ["exact.md"]
