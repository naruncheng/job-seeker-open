from __future__ import annotations

import pytest

from jobs.score.budget import BudgetExceeded, BudgetGuard, estimate_cost_usd


def test_estimate_cost_uses_per_token_pricing() -> None:
    cost = estimate_cost_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(1.00 + 5.00)


def test_estimate_cost_includes_cache_tokens() -> None:
    cost = estimate_cost_usd(
        "claude-haiku-4-5",
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )
    # cache write ~1.25x input rate, cache read ~0.1x input rate
    assert cost == pytest.approx(1.00 * 1.25 + 1.00 * 0.1)


def test_estimate_cost_unknown_model_raises() -> None:
    with pytest.raises(ValueError):
        estimate_cost_usd("not-a-real-model", 100, 100)


def test_budget_guard_allows_calls_under_cap() -> None:
    guard = BudgetGuard(cap_usd=1.0)
    guard.check_before_call()  # should not raise
    guard.record(0.5)
    guard.check_before_call()  # still under cap


def test_budget_guard_raises_once_cap_is_reached() -> None:
    guard = BudgetGuard(cap_usd=1.0)
    guard.record(1.0)
    with pytest.raises(BudgetExceeded):
        guard.check_before_call()


def test_budget_guard_raises_when_over_cap() -> None:
    guard = BudgetGuard(cap_usd=1.0)
    guard.record(1.5)
    with pytest.raises(BudgetExceeded):
        guard.check_before_call()
