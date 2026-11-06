from __future__ import annotations

from jobs.digest.grouping import group_jobs, score_band


def _job(**overrides: object) -> dict:
    defaults = dict(
        job_id="abc",
        title="Engineer",
        company="Acme",
        url="https://example.com",
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


def test_group_jobs_orders_scopes_local_domestic_international() -> None:
    jobs = [
        _job(job_id="a", scope="international"),
        _job(job_id="b", scope="local"),
        _job(job_id="c", scope="domestic"),
    ]
    grouped = group_jobs(jobs)
    assert list(grouped.keys()) == ["local", "domestic", "international"]


def test_group_jobs_nests_by_industry_then_role_type() -> None:
    jobs = [
        _job(job_id="a", industry="Robotics", role_type="Graduate Program"),
        _job(job_id="b", industry="Robotics", role_type="Internship"),
        _job(job_id="c", industry="Software", role_type="Graduate Program"),
    ]
    grouped = group_jobs(jobs)
    assert set(grouped["local"].keys()) == {"Robotics", "Software"}
    assert set(grouped["local"]["Robotics"].keys()) == {"Graduate Program", "Internship"}


def test_group_jobs_drops_empty_scopes() -> None:
    jobs = [_job(job_id="a", scope="local")]
    grouped = group_jobs(jobs)
    assert "domestic" not in grouped
    assert "international" not in grouped


def test_group_jobs_sorts_by_relevance_descending_within_group() -> None:
    jobs = [
        _job(job_id="low", relevance=7),
        _job(job_id="high", relevance=10),
        _job(job_id="mid", relevance=8),
    ]
    grouped = group_jobs(jobs)
    ids = [j["job_id"] for j in grouped["local"]["Robotics"]["Graduate Program"]]
    assert ids == ["high", "mid", "low"]


def test_group_jobs_defaults_missing_industry_and_role_type() -> None:
    jobs = [_job(job_id="a", industry=None, role_type=None)]
    grouped = group_jobs(jobs)
    assert "Other" in grouped["local"]
    assert "Other" in grouped["local"]["Other"]


def test_group_jobs_empty_input() -> None:
    assert group_jobs([]) == {}


def test_score_band_thresholds() -> None:
    assert score_band(10) == "excellent"
    assert score_band(9) == "excellent"
    assert score_band(8) == "good"
    assert score_band(7) == "good"
    assert score_band(6) == "borderline"
    assert score_band(0) == "borderline"
