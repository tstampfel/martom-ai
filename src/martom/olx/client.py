"""Async client for the public OLX.ba API, with the politeness rules from docs/PLAN.md.

- At most one request start every 1.3 s, shared by every caller of one `RateLimiter`.
- HTTP 429 and 5xx are retried with exponential backoff, honouring `Retry-After`.
- Timeouts and connection errors are retried the same way; other 4xx are not retried.
- Every retry still goes through the limiter, so retries never break the 1.3 s rule.

The clock, sleep and "now" functions are constructor arguments so tests can replace them
with fakes and never wait for real.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx

from martom import __version__

logger = logging.getLogger(__name__)

# Defaults match .env.example (OLX_API_BASE_URL, OLX_SHOP_USERNAME,
# OLX_MIN_REQUEST_INTERVAL_SECONDS). Settings wiring arrives with M0-4.
DEFAULT_BASE_URL = "https://api.olx.ba"
DEFAULT_SHOP_USERNAME = "martom"
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 1.3

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OlxApiError(Exception):
    """A request to OLX failed for good (not retryable, or out of attempts).

    `status_code` is the HTTP status, or None when no response arrived (timeout,
    connection error); in that case `__cause__` holds the original httpx exception.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimiter:
    """Keeps request starts at least `min_interval_seconds` apart, across concurrent callers.

    Callers `await wait()` right before sending a request. The lock makes concurrent
    callers queue up one at a time (like a mutex around "check the time, maybe sleep,
    claim the next slot"), so two coroutines can never claim the same slot.
    """

    def __init__(
        self,
        min_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_start: float | None = None  # earliest moment the next request may start

    async def wait(self) -> None:
        """Sleep until the next request may start, then claim that slot."""
        async with self._lock:
            # A loop, not an `if`: `pause()` can push `_next_start` later while we sleep.
            while self._next_start is not None and (now := self._clock()) < self._next_start:
                await self._sleep(self._next_start - now)
            self._next_start = self._clock() + self._min_interval

    def pause(self, seconds: float) -> None:
        """Let no request start for `seconds` from now (used after 429/5xx).

        It only ever moves the next start later, never earlier, so a short Retry-After
        cannot break the minimum interval.
        """
        resume_at = self._clock() + seconds
        if self._next_start is None or resume_at > self._next_start:
            self._next_start = resume_at


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Turn a `Retry-After` header into seconds to wait, or None if missing or invalid.

    The header is either whole seconds ("120") or an HTTP date
    ("Thu, 26 Nov 2026 12:00:20 GMT"). A date in the past means "retry now" (0.0).
    """
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:  # "-0000" dates parse as naive; HTTP dates are always UTC
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - now).total_seconds())


class OlxClient:
    """Async client for the two OLX endpoints we use.

    Use it as an async context manager so the connection pool is closed:

        async with OlxClient() as olx:
            page = await olx.get_shop_listings({"category_id": 31, "page": 1})

    Share one client (or one `RateLimiter`) for the whole sync: the 1.3 s rule is
    enforced per limiter, so two clients with their own limiters would together go
    twice as fast.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        shop_username: str = DEFAULT_SHOP_USERNAME,
        min_request_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = 10.0,
        max_attempts: int = 5,
        backoff_base_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
        max_retry_after_seconds: float = 300.0,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        now: Now = _utc_now,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._shop_username = shop_username
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._max_backoff = max_backoff_seconds
        self._max_retry_after = max_retry_after_seconds
        self._now = now
        self._limiter = rate_limiter or RateLimiter(
            min_request_interval_seconds, clock=clock, sleep=sleep
        )
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": f"martom-ai/{__version__} (+https://github.com/tstampfel/martom-ai)",
            },
        )

    # `async with OlxClient() as olx:` calls these two, like try/finally around the body.
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_shop_listings(self, params: Mapping[str, str | int] | None = None) -> Any:
        """GET /users/{shop}/listings with query params passed through (paging is M1-2)."""
        return await self._get(f"/users/{self._shop_username}/listings", params=params)

    async def get_listing(self, listing_id: int) -> Any:
        """GET /listings/{id}: one listing's details (parsing them is M1-3)."""
        return await self._get(f"/listings/{listing_id}")

    async def _get(self, path: str, *, params: Mapping[str, str | int] | None = None) -> Any:
        """Send a GET with pacing and retries; return the decoded JSON body."""
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.wait()
            retry_after: float | None = None
            try:
                response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:  # timeouts and connection errors
                failure = OlxApiError(f"GET {path} failed: {exc!r}")
                failure.__cause__ = exc
            else:
                status = response.status_code
                if response.is_success:
                    return response.json()
                failure = OlxApiError(f"GET {path} returned HTTP {status}", status_code=status)
                if status != 429 and status < 500:
                    raise failure  # other 4xx: retrying won't help
                retry_after = parse_retry_after(
                    response.headers.get("Retry-After"), now=self._now()
                )

            if attempt == self._max_attempts:
                raise failure
            if retry_after is not None and retry_after > self._max_retry_after:
                raise OlxApiError(
                    f"{failure} with Retry-After {retry_after:.0f} s, longer than the "
                    f"{self._max_retry_after:.0f} s we are willing to wait",
                    status_code=failure.status_code,
                ) from failure
            delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
            logger.warning(
                "%s; retry %d/%d in %.1f s", failure, attempt, self._max_attempts - 1, delay
            )
            self._limiter.pause(delay)

        raise AssertionError("unreachable: the loop always returns or raises")

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: base, 2 * base, 4 * base, ... capped at max_backoff."""
        return min(self._backoff_base * 2 ** (attempt - 1), self._max_backoff)
