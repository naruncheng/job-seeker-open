from __future__ import annotations

from typing import Any

import pytest


class FakeFetchClient:
    """Stands in for FetchClient in tests: returns canned JSON, records calls."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> object:
        self.calls.append((url, params))
        return self.response

    def close(self) -> None:
        pass


@pytest.fixture
def fake_client() -> type[FakeFetchClient]:
    return FakeFetchClient
