"""Shared HTTP plumbing for ingest sources: rate limiting, retries, robots.txt,
and an on-disk cache so repeated dev runs don't re-hit live sites.

Every source module should go through `FetchClient` rather than calling
httpx directly, so the CLAUDE.md §7 guardrails (rate limits, robots.txt,
descriptive User-Agent, backoff, request cap) apply uniformly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from jobs.config import DATA_DIR, user_agent

logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "cache"
DEFAULT_CACHE_TTL_SECONDS = 6 * 3600
DEFAULT_MIN_DELAY_SECONDS = 2.0
DEFAULT_MAX_REQUESTS_PER_SOURCE = 200
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4


class SourceBudgetExceeded(RuntimeError):
    """Raised when a source hits its per-run request cap."""


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids fetching a URL."""


class RateLimiter:
    """Enforces a minimum delay between requests to the same domain."""

    def __init__(self, min_delay_seconds: float = DEFAULT_MIN_DELAY_SECONDS) -> None:
        self.min_delay_seconds = min_delay_seconds
        self._last_request_at: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        last = self._last_request_at.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = self.min_delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[domain] = time.monotonic()


class RobotsCache:
    """Fetches and caches robots.txt parsers per domain."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str, agent: str) -> bool:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._parsers.get(domain)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self._client.get(f"{domain}/robots.txt", timeout=10)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    # No robots.txt (or unreadable) is treated as "allow" —
                    # standard robots.txt convention.
                    parser.parse([])
            except httpx.HTTPError:
                logger.warning("robots.txt fetch failed for %s; assuming allowed", domain)
                parser.parse([])
            self._parsers[domain] = parser
        return parser.can_fetch(agent, url)


class DiskCache:
    """Flat-file JSON response cache keyed by URL + params."""

    def __init__(self, cache_dir: Path = CACHE_DIR, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, key: str) -> object | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: object) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")


class FetchClient:
    """A rate-limited, robots-respecting, cached, retrying JSON HTTP client."""

    def __init__(
        self,
        min_delay_seconds: float = DEFAULT_MIN_DELAY_SECONDS,
        max_requests: int = DEFAULT_MAX_REQUESTS_PER_SOURCE,
        use_cache: bool = True,
        respect_robots: bool = True,
    ) -> None:
        self._client = httpx.Client(headers={"User-Agent": user_agent()}, follow_redirects=True)
        self._rate_limiter = RateLimiter(min_delay_seconds)
        self._robots = RobotsCache(self._client)
        self._cache = DiskCache() if use_cache else None
        self._respect_robots = respect_robots
        self.max_requests = max_requests
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FetchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
    ) -> object:
        cache_key = json.dumps({"url": url, "params": params, "method": method, "body": json_body})
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        agent = self._client.headers["User-Agent"]
        if self._respect_robots and not self._robots.allowed(url, agent):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

        if self.request_count >= self.max_requests:
            raise SourceBudgetExceeded(
                f"hit max_requests={self.max_requests} for this source's run"
            )

        domain = urlparse(url).netloc
        response = self._request_with_retry(method, url, domain, params, json_body)
        result = response.json()

        if self._cache is not None:
            self._cache.set(cache_key, result)
        return result

    def get_text(self, url: str) -> str:
        """Like `get_json`, but for a page whose body is HTML/plain text,
        not JSON (e.g. an arbitrary job ad a user pasted a link to)."""
        cache_key = json.dumps({"url": url, "kind": "text"})
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if isinstance(cached, str):
                return cached

        agent = self._client.headers["User-Agent"]
        if self._respect_robots and not self._robots.allowed(url, agent):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

        if self.request_count >= self.max_requests:
            raise SourceBudgetExceeded(
                f"hit max_requests={self.max_requests} for this source's run"
            )

        domain = urlparse(url).netloc
        response = self._request_with_retry("GET", url, domain, None, None)
        result = response.text

        if self._cache is not None:
            self._cache.set(cache_key, result)
        return result

    def _request_with_retry(
        self,
        method: str,
        url: str,
        domain: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> httpx.Response:
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limiter.wait(domain)
            self.request_count += 1
            try:
                resp = self._client.request(method, url, params=params, json=json_body, timeout=20)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("request error on attempt %d for %s: %s", attempt, url, exc)
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    resp.raise_for_status()
                    return resp
                last_exc = httpx.HTTPStatusError(
                    f"retryable status {resp.status_code}", request=resp.request, response=resp
                )
                logger.warning(
                    "retryable status %d on attempt %d for %s", resp.status_code, attempt, url
                )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
        assert last_exc is not None
        raise last_exc
