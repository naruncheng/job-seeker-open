from __future__ import annotations

from jobs.letters.coverage import term_coverage
from jobs.letters.schema import RequirementEvidence, RequirementMapping


def _item(terms: list[str], covered: bool = True) -> RequirementEvidence:
    return RequirementEvidence(
        requirement=terms[0], jd_terms=terms, evidence="x" if covered else None, covered=covered
    )


def test_counts_only_covered_requirements() -> None:
    mapping = RequirementMapping(
        items=[
            _item(["root cause analysis"]),
            _item(["maintenance planning tools"], covered=False),
        ]
    )
    coverage = term_coverage("I did root cause analysis on the enclosure.", mapping)
    assert coverage.used == ["root cause analysis"]
    assert coverage.missed == []
    assert coverage.total == 1


def test_any_one_of_a_requirements_terms_counts() -> None:
    mapping = RequirementMapping(items=[_item(["fault-finding", "troubleshooting"])])
    assert term_coverage("Troubleshooting thermal issues.", mapping).used == ["troubleshooting"]


def test_tolerates_case_hyphens_and_plurals() -> None:
    mapping = RequirementMapping(
        items=[_item(["Fault-finding"]), _item(["conveyor"]), _item(["CAD"])]
    )
    letter = "Hands-on fault finding across conveyors, all modelled in cad."
    assert len(term_coverage(letter, mapping).used) == 3


def test_does_not_match_inside_other_words() -> None:
    mapping = RequirementMapping(items=[_item(["CAD"])])
    coverage = term_coverage("A cadence of weekly reviews.", mapping)
    assert coverage.used == []
    assert coverage.missed_terms() == ["CAD"]


def test_stock_phrases_ignore_case_and_apostrophe_style() -> None:
    from jobs.letters.coverage import stock_phrases_in

    letter = "I’m confident in my ability to ADAPT quickly, and more."
    phrases = ["I'm confident in my ability to adapt quickly", "a leading company like"]
    assert stock_phrases_in(letter, phrases) == ["I'm confident in my ability to adapt quickly"]


AD = (
    "You will be accountable for interaction with inter discipline engineers to "
    "execute multidiscipline projects, and a strong commitment to client "
    "satisfaction and quality of work. Communicates clearly and effectively, "
    "adapting communication to different audiences."
)


def test_echo_flags_a_sentence_copied_from_the_ad() -> None:
    from jobs.letters.coverage import echoed_from_ad

    letter = (
        "As team lead I was interacting with inter-discipline engineers to "
        "execute multidiscipline projects."
    )
    assert echoed_from_ad(letter, AD) == [
        "with inter discipline engineers to execute multidiscipline projects"
    ]


def test_echo_ignores_short_key_terms() -> None:
    from jobs.letters.coverage import echoed_from_ad

    letter = "Both projects showed my focus on client satisfaction and quality of work."
    assert echoed_from_ad(letter, AD) == []


def test_echo_allows_quoting_selection_criteria() -> None:
    from jobs.letters.coverage import echoed_from_ad

    criterion = (
        "Communicates clearly and effectively, adapting communication to different audiences"
    )
    letter = "I communicate clearly and effectively, adapting communication to different audiences."
    assert echoed_from_ad(letter, AD) != []
    assert echoed_from_ad(letter, AD, allowed=[criterion]) == []
