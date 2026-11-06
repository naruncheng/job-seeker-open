"""Tracks estimated LLM spend for a scoring run and enforces the cap.

CLAUDE.md §7: "Budget guard: track LLM spend per run, abort the run if a
single run would exceed the cap in settings (default $1)." Pricing below is
Anthropic's first-party per-token rate as of this writing (see DECISIONS.md);
cache read/write multipliers are the standard ~0.1x / ~1.25x of the input
rate and are an approximation, not billed-exact.
"""

from __future__ import annotations

# USD per token (not per-million) for input/output, keyed by model id.
PRICING_PER_TOKEN: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00 / 1_000_000, "output": 5.00 / 1_000_000},
    "claude-sonnet-5": {"input": 2.00 / 1_000_000, "output": 10.00 / 1_000_000},
}

_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.1


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    prices = PRICING_PER_TOKEN.get(model)
    if prices is None:
        raise ValueError(f"no pricing known for model {model!r}")
    return (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + cache_creation_input_tokens * prices["input"] * _CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * prices["input"] * _CACHE_READ_MULTIPLIER
    )


class BudgetExceeded(RuntimeError):
    """Raised when the next call would (or already did) exceed the run's cap."""


class BudgetGuard:
    """Accumulates spend across a run and refuses to start another call over cap."""

    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = 0.0

    def check_before_call(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise BudgetExceeded(
                f"spent ${self.spent_usd:.4f}, cap is ${self.cap_usd:.4f} — aborting run"
            )

    def record(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd
