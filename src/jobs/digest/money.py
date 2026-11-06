"""Tiny formatter for the LLM spend shown in the digest footer."""

from __future__ import annotations


def format_usd(amount: float | None) -> str | None:
    """0.02893 -> '$0.029'. Returns None when the cost isn't known, so the
    footer just omits it instead of showing a misleading $0."""
    if amount is None:
        return None
    return f"${amount:.3f}"
