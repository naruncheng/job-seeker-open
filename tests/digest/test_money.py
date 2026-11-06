from __future__ import annotations

import pytest

from jobs.digest.money import format_usd


@pytest.mark.parametrize(
    "amount,expected",
    [(0.02893, "$0.029"), (0.0, "$0.000"), (0.4, "$0.400"), (1.2345, "$1.234"), (None, None)],
)
def test_format_usd(amount: float | None, expected: str | None) -> None:
    assert format_usd(amount) == expected
