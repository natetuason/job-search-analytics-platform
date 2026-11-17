"""Shared HTTP client for sources: per-client rate limiting, retry with backoff, timeouts."""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "job-platform/0.1 (personal job search; contact via repo owner)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class AccessPolicy:
    """How politely a source may be accessed, and under what terms."""

    name: str
    min_interval_s: float  # minimum gap between requests
    max_retries: int = 4
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0
    timeout_s: float = 20.0
    terms: str = ""


class SourceError(Exception):
    """A fetch failed in a way the caller should record (not crash on)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpClient:
    def __init__(
        self,
        policy: AccessPolicy,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.policy = policy
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests_made = 0  # every attempt, including retries (some APIs bill per call)
        self._client = httpx.Client(
            transport=transport,
            timeout=policy.timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.policy.min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self.policy.backoff_max_s)
        delay = self.policy.backoff_base_s * (2**attempt)
        return min(delay + random.uniform(0, delay / 2), self.policy.backoff_max_s)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._get(url, params)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"{self.policy.name}: response was not JSON", resp.status_code) from exc

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> tuple[str, str]:
        """Return (body, content type)."""
        resp = self._get(url, params)
        return resp.text, resp.headers.get("content-type", "")

    def _get(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        attempt = 0
        while True:
            self._throttle()
            try:
                self.requests_made += 1
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt >= self.policy.max_retries:
                    raise SourceError(f"{self.policy.name}: network error after retries: {type(exc).__name__}") from exc
                delay = self._backoff(attempt, None)
                logger.warning("transient network error, retrying", extra={"fields": {"source": self.policy.name, "delay_s": round(delay, 2)}})
            else:
                if resp.status_code == 200:
                    return resp
                if resp.status_code not in RETRY_STATUSES:
                    raise SourceError(f"{self.policy.name}: HTTP {resp.status_code}", resp.status_code)
                if attempt >= self.policy.max_retries:
                    raise SourceError(f"{self.policy.name}: HTTP {resp.status_code} after retries", resp.status_code)
                delay = self._backoff(attempt, resp.headers.get("Retry-After"))
                logger.warning("retryable HTTP status", extra={"fields": {"source": self.policy.name, "status": resp.status_code, "delay_s": round(delay, 2)}})
            self._sleep(delay)
            attempt += 1
