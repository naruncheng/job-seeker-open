from __future__ import annotations

import httpx
from pytest_httpx import HTTPXMock

from jobs.letters.company_context import MAX_CONTEXT_CHARS, fetch_company_context


def test_fetch_company_context_returns_stripped_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://example.com",
        html="<html><body><h1>Acme</h1><p>We build robots.</p></body></html>",
    )

    result = fetch_company_context("https://example.com/jobs/123")

    assert result is not None
    assert "Acme" in result
    assert "<" not in result


def test_fetch_company_context_respects_robots_disallow(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/robots.txt",
        text="User-agent: *\nDisallow: /",
    )

    result = fetch_company_context("https://example.com/jobs/123")

    assert result is None


def test_fetch_company_context_returns_none_on_network_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("boom"), url="https://example.com/robots.txt")

    result = fetch_company_context("https://example.com/jobs/123")

    assert result is None


def test_fetch_company_context_returns_none_for_bad_url() -> None:
    assert fetch_company_context("not-a-url") is None


def test_fetch_company_context_truncates_long_pages(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    long_html = "<p>" + ("word " * 5000) + "</p>"
    httpx_mock.add_response(url="https://example.com", html=long_html)

    result = fetch_company_context("https://example.com/jobs/123")

    assert result is not None
    assert len(result) <= MAX_CONTEXT_CHARS
