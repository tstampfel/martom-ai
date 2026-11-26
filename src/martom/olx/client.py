"""Async client for the public OLX.ba API, with the politeness rules from docs/PLAN.md.

- At most one request start every 1.3 s, shared by every caller of one `RateLimiter`.
- HTTP 429 and 5xx are retried with exponential backoff, honouring `Retry-After`.
- Timeouts, connection errors and decoding errors are retried the same way; other 4xx
  and 2xx bodies that are not JSON are not retried.
- Every retry still goes through the limiter, so retries never break the 1.3 s rule.
- Two limits on Retry-After:
  - `max_retry_after_seconds` (default 300 s) is how long one call is willing to wait.
    Above it the call gives up with `OlxApiError`, but the shared limiter still pauses
    for the full Retry-After, so nobody contacts OLX before it allows.
  - `max_limiter_pause_seconds` (default 3600 s) is the hard ceiling. A Retry-After above
    it, or one that is non-finite or unparseable, raises `OlxRateLimitAbortError` and stops
    the limiter for good: every later call on it refuses without contacting OLX.

The clock, sleep and "now" functions are constructor arguments so tests can replace them
with fakes and never wait for real.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, NoReturn, Self

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

    `status_code` is the HTTP status, or None when no usable response arrived (timeout,
    connection error, undecodable body); in that case `__cause__` holds the original
    httpx exception.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OlxRateLimitAbortError(OlxApiError):
    """OLX asked us to stop for longer than we will ever wait, or sent a nonsense Retry-After.

    Raised when a 429/5xx carries a Retry-After above `max_limiter_pause_seconds`
    (default 1 hour), a non-finite one, or one that can't be parsed. The client does not
    sleep; it stops its `RateLimiter`, so every later call through that limiter (from any
    client sharing it) raises this too, without sending a request.

    For the sync job (M1-4): treat it as "end this run now". Catch it at the top, log it
    as an error and exit non-zero. Listings already synced stay as they are; the next
    scheduled run starts with a fresh client and limiter and tries again.
    """


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
        self._abort_reason: str | None = None  # set by `abort()`; never cleared

    async def wait(self) -> None:
        """Sleep until the next request may start, then claim that slot.

        Raises `OlxRateLimitAbortError` once `abort()` has been called, even mid-wait.
        """
        async with self._lock:
            self._raise_if_aborted()
            # A loop, not an `if`: `pause()` can push `_next_start` later while we sleep.
            while self._next_start is not None and (now := self._clock()) < self._next_start:
                await self._sleep(self._next_start - now)
                self._raise_if_aborted()
            self._next_start = self._clock() + self._min_interval

    def abort(self, reason: str) -> None:
        """Stop this limiter for good: every later `wait()` raises `OlxRateLimitAbortError`."""
        if self._abort_reason is None:
            self._abort_reason = reason

    def _raise_if_aborted(self) -> None:
        if self._abort_reason is not None:
            raise OlxRateLimitAbortError(
                f"OLX requests stopped for this run: {self._abort_reason}", status_code=None
            )

    def pause(self, seconds: float) -> None:
        """Let no request start for `seconds` from now (used after 429/5xx).

        It only ever moves the next start later, never earlier, so a short Retry-After
        cannot break the minimum interval.
        """
        resume_at = self._clock() + seconds
        if self._next_start is None or resume_at > self._next_start:
            self._next_start = resume_at


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Turn a `Retry-After` header into seconds to wait; None if there is no header.

    The header is either whole seconds ("120") or an HTTP date
    ("Thu, 26 Nov 2026 12:00:20 GMT"). A date in the past means "retry now" (0.0).
    Anything else (empty, negative, "inf", text) raises `ValueError`. Valid but absurd
    values pass through unchanged (a 400-digit number becomes `math.inf`); `OlxClient`
    decides what to do with them.
    """
    if value is None:
        return None
    text = value.strip()
    if text.isascii() and text.isdigit():  # isdigit() alone accepts "²", which float() rejects
        return float(text)
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unparseable Retry-After {value[:50]!r}") from exc
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

    When you pass `rate_limiter`, it takes precedence: `min_request_interval_seconds`,
    `clock` and `sleep` are then ignored for pacing, because the shared limiter already
    has its own.

    Errors: every failure raises `OlxApiError`. Its subclass `OlxRateLimitAbortError` means
    "stop the whole run" (see its docstring); after it, every call on this client and on
    any client sharing its limiter raises `OlxRateLimitAbortError` without contacting OLX.
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
        max_limiter_pause_seconds: float = 3600.0,
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
        self._max_limiter_pause = max_limiter_pause_seconds
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
            except httpx.RequestError as exc:  # timeouts, connection and decoding errors
                failure = OlxApiError(f"GET {path} failed: {exc!r}")
                failure.__cause__ = exc
            else:
                status = response.status_code
                if response.is_success:
                    try:
                        return response.json()
                    except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
                        raise OlxApiError(
                            f"GET {path} returned a body that is not JSON", status_code=status
                        ) from exc
                failure = OlxApiError(f"GET {path} returned HTTP {status}", status_code=status)
                if status != 429 and status < 500:
                    raise failure  # other 4xx: retrying won't help
                # Raises OlxRateLimitAbortError for an absurd or unparseable header.
                retry_after = self._checked_retry_after(response, failure)

            delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
            # Pause the shared limiter BEFORE either give-up below, so whoever sends the
            # next request through this limiter (another coroutine, or the sync moving on
            # after catching OlxApiError) still waits as long as OLX asked. The delay is
            # at most `max_limiter_pause_seconds`: anything longer aborted above.
            self._limiter.pause(delay)

            if retry_after is not None and retry_after > self._max_retry_after:
                over_cap = OlxApiError(
                    f"{failure} with Retry-After {retry_after:.0f} s, longer than the "
                    f"{self._max_retry_after:.0f} s cap",
                    status_code=failure.status_code,
                )
                logger.warning("%s; giving up; limiter paused %.1f s", over_cap, delay)
                raise over_cap from failure
            if attempt == self._max_attempts:
                logger.warning("%s; giving up; limiter paused %.1f s", failure, delay)
                raise failure
            logger.warning(
                "%s; retry %d/%d in %.1f s", failure, attempt, self._max_attempts - 1, delay
            )

        raise AssertionError("unreachable: the loop always returns or raises")

    def _checked_retry_after(self, response: httpx.Response, failure: OlxApiError) -> float | None:
        """Return the response's Retry-After in seconds (None if absent), or abort.

        Aborts (see `OlxRateLimitAbortError`) when the header can't be parsed, or asks for more
        than `max_limiter_pause_seconds`; `inf` is caught by the same comparison.
        """
        raw = response.headers.get("Retry-After")
        try:
            seconds = parse_retry_after(raw, now=self._now())
        except ValueError as exc:
            self._abort(failure, str(exc))
        if seconds is not None and seconds > self._max_limiter_pause:
            assert raw is not None  # a parsed value means the header was there
            self._abort(
                failure,
                f"Retry-After {raw[:50]!r} is longer than the "
                f"{self._max_limiter_pause:.0f} s limiter ceiling",
            )
        return seconds

    def _abort(self, failure: OlxApiError, detail: str) -> NoReturn:
        """Stop the shared limiter, log at error level and raise `OlxRateLimitAbortError`."""
        reason = f"{failure}: {detail}"
        self._limiter.abort(reason)
        logger.error("%s; aborting: no more OLX requests through this limiter", reason)
        raise OlxRateLimitAbortError(
            f"{reason}; aborting: no more OLX requests through this limiter",
            status_code=failure.status_code,
        ) from failure

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: base, 2 * base, 4 * base, ... capped at max_backoff."""
        return min(self._backoff_base * 2 ** (attempt - 1), self._max_backoff)
