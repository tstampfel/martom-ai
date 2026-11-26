"""Unit tests for the OLX API client.

No network and no real sleeping: every test uses `httpx.MockTransport` (a fake transport
that hands each request to a Python function) and `FakeClock`, whose `sleep` moves a fake
monotonic clock forward instead of waiting.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from itertools import pairwise

import httpx
import pytest

from martom.olx.client import OlxApiError, OlxClient, RateLimiter, parse_retry_after

# The anyio pytest plugin (installed with httpx) runs `async def` tests on asyncio.
pytestmark = pytest.mark.anyio

FIXED_NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    """A monotonic clock that only moves when someone sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)  # yield, so concurrent callers really interleave


Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class Recorder:
    """Fake OLX server: replays `responses` in order and records when each request started."""

    def __init__(self, clock: FakeClock, responses: list[httpx.Response | Exception]) -> None:
        self.clock = clock
        self.responses = list(responses)
        self.starts: list[float] = []
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.starts.append(self.clock.monotonic())
        self.requests.append(request)
        await asyncio.sleep(0)  # a real request yields while it waits for the server
        result = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(result, Exception):
            raise result
        return result


def ok(payload: object | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload if payload is not None else {"data": []})


def make_client(clock: FakeClock, handler: Handler, **kwargs: object) -> OlxClient:
    return OlxClient(
        transport=httpx.MockTransport(handler),
        clock=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: FIXED_NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def gaps(starts: list[float]) -> list[float]:
    return [round(b - a, 6) for a, b in pairwise(starts)]


# --- Endpoints -------------------------------------------------------------------------


async def test_get_shop_listings_calls_the_shop_endpoint_with_params() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok({"data": [{"id": 1}], "meta": {"total": 1}})])

    async with make_client(clock, server) as client:
        body = await client.get_shop_listings({"page": 2, "category_id": 31})

    assert body == {"data": [{"id": 1}], "meta": {"total": 1}}
    request = server.requests[0]
    assert request.method == "GET"
    assert request.url.host == "api.olx.ba"
    assert request.url.path == "/users/martom/listings"
    assert request.url.params["page"] == "2"
    assert request.url.params["category_id"] == "31"


async def test_get_listing_calls_the_detail_endpoint() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok({"id": 63996799, "title": "Panel"})])

    async with make_client(clock, server) as client:
        body = await client.get_listing(63996799)

    assert body == {"id": 63996799, "title": "Panel"}
    assert server.requests[0].url.path == "/listings/63996799"


async def test_base_url_and_shop_username_are_configurable() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(
        clock, server, base_url="https://olx.example.test", shop_username="drugi"
    ) as client:
        await client.get_shop_listings()

    assert str(server.requests[0].url) == "https://olx.example.test/users/drugi/listings"


# --- Pacing ----------------------------------------------------------------------------


async def test_first_request_does_not_wait() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)

    assert clock.sleeps == []


async def test_sequential_requests_start_at_least_1_3_seconds_apart() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server) as client:
        for listing_id in range(4):
            await client.get_listing(listing_id)

    assert gaps(server.starts) == [1.3, 1.3, 1.3]


async def test_concurrent_callers_share_the_limit() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server) as client:
        await asyncio.gather(*(client.get_listing(i) for i in range(5)))

    assert len(server.starts) == 5
    assert gaps(server.starts) == [1.3, 1.3, 1.3, 1.3]


async def test_no_wait_when_enough_time_already_passed() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)
        clock.now += 5.0  # the caller spent 5 s doing other work
        await client.get_listing(2)

    assert clock.sleeps == []


async def test_min_interval_is_configurable() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server, min_request_interval_seconds=2.5) as client:
        await client.get_listing(1)
        await client.get_listing(2)

    assert gaps(server.starts) == [2.5]


async def test_pause_while_a_caller_is_waiting_delays_that_caller() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.3, clock=clock.monotonic, sleep=clock.sleep)
    await limiter.wait()  # claims the first slot

    waiter = asyncio.create_task(limiter.wait())
    await asyncio.sleep(0)  # let the waiter start sleeping towards its 1.3 s slot
    paused_at = clock.now
    limiter.pause(10.0)
    await waiter

    assert clock.now >= paused_at + 10.0


async def test_pause_never_moves_the_next_start_earlier() -> None:
    clock = FakeClock()
    limiter = RateLimiter(5.0, clock=clock.monotonic, sleep=clock.sleep)
    await limiter.wait()

    limiter.pause(1.0)
    await limiter.wait()

    assert clock.sleeps == [5.0]


async def test_two_clients_can_share_one_limiter() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])
    limiter = RateLimiter(1.3, clock=clock.monotonic, sleep=clock.sleep)

    async with (
        make_client(clock, server, rate_limiter=limiter) as first,
        make_client(clock, server, rate_limiter=limiter) as second,
    ):
        await first.get_listing(1)
        await second.get_listing(2)

    assert gaps(server.starts) == [1.3]


# --- 429 and Retry-After ---------------------------------------------------------------


async def test_429_with_retry_after_seconds_waits_that_long_then_retries() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(429, headers={"Retry-After": "7"}), ok({"id": 1})])

    async with make_client(clock, server) as client:
        body = await client.get_listing(1)

    assert body == {"id": 1}
    assert gaps(server.starts) == [7.0]


async def test_429_with_retry_after_http_date_waits_until_that_date() -> None:
    clock = FakeClock()
    retry_at = "Thu, 26 Nov 2026 12:00:20 GMT"  # FIXED_NOW + 20 s
    server = Recorder(clock, [httpx.Response(429, headers={"Retry-After": retry_at}), ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)

    assert gaps(server.starts) == [20.0]


async def test_429_without_retry_after_uses_exponential_backoff() -> None:
    clock = FakeClock()
    too_many = httpx.Response(429)
    server = Recorder(clock, [too_many, too_many, too_many, ok()])

    async with make_client(clock, server, backoff_base_seconds=2.0) as client:
        await client.get_listing(1)

    assert gaps(server.starts) == [2.0, 4.0, 8.0]


async def test_short_retry_after_never_breaks_the_minimum_interval() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(429, headers={"Retry-After": "0"}), ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)

    assert gaps(server.starts) == [1.3]


async def test_429_pauses_other_concurrent_callers_too() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(429, headers={"Retry-After": "10"}), ok(), ok(), ok()])

    async with make_client(clock, server) as client:
        await asyncio.gather(client.get_listing(1), client.get_listing(2))

    # Request 2 was already waiting for its 1.3 s slot when request 1 got the 429;
    # it must still hold off for the full 10 s.
    assert server.starts[1] - server.starts[0] >= 10.0


async def test_429_gives_up_after_max_attempts() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(429)])

    async with make_client(clock, server, max_attempts=3) as client:
        with pytest.raises(OlxApiError) as caught:
            await client.get_listing(1)

    assert caught.value.status_code == 429
    assert len(server.starts) == 3


# --- 5xx -------------------------------------------------------------------------------


async def test_5xx_retries_with_exponential_backoff_then_succeeds() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(500), httpx.Response(503), ok({"id": 9})])

    async with make_client(clock, server, backoff_base_seconds=2.0) as client:
        body = await client.get_listing(9)

    assert body == {"id": 9}
    assert gaps(server.starts) == [2.0, 4.0]


async def test_5xx_gives_up_after_max_attempts() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(502)])

    async with make_client(clock, server, max_attempts=4) as client:
        with pytest.raises(OlxApiError) as caught:
            await client.get_listing(1)

    assert caught.value.status_code == 502
    assert len(server.starts) == 4


async def test_backoff_is_capped() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(500)])

    async with make_client(
        clock, server, max_attempts=6, backoff_base_seconds=2.0, max_backoff_seconds=10.0
    ) as client:
        with pytest.raises(OlxApiError):
            await client.get_listing(1)

    assert gaps(server.starts) == [2.0, 4.0, 8.0, 10.0, 10.0]


async def test_5xx_honours_retry_after_too() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(503, headers={"Retry-After": "30"}), ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)

    assert gaps(server.starts) == [30.0]


async def test_retry_after_longer_than_the_cap_raises_instead_of_waiting() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(429, headers={"Retry-After": "3600"}), ok()])

    async with make_client(clock, server, max_retry_after_seconds=300.0) as client:
        with pytest.raises(OlxApiError) as caught:
            await client.get_listing(1)

    assert caught.value.status_code == 429
    assert len(server.starts) == 1


# --- Other 4xx -------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
async def test_other_4xx_errors_are_not_retried(status: int) -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.Response(status), ok()])

    async with make_client(clock, server) as client:
        with pytest.raises(OlxApiError) as caught:
            await client.get_listing(1)

    assert caught.value.status_code == status
    assert len(server.starts) == 1


# --- Timeouts and connection errors ----------------------------------------------------


async def test_client_sets_a_timeout_on_every_request() -> None:
    clock = FakeClock()
    server = Recorder(clock, [ok()])

    async with make_client(clock, server, timeout_seconds=12.5) as client:
        await client.get_listing(1)

    timeouts = server.requests[0].extensions["timeout"]
    assert timeouts == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}


async def test_timeout_is_retried_with_backoff() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.ReadTimeout("slow"), ok({"id": 1})])

    async with make_client(clock, server, backoff_base_seconds=2.0) as client:
        body = await client.get_listing(1)

    assert body == {"id": 1}
    assert gaps(server.starts) == [2.0]


async def test_repeated_timeouts_raise_after_max_attempts() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.ConnectTimeout("down")])

    async with make_client(clock, server, max_attempts=3) as client:
        with pytest.raises(OlxApiError) as caught:
            await client.get_listing(1)

    assert caught.value.status_code is None
    assert isinstance(caught.value.__cause__, httpx.ConnectTimeout)
    assert len(server.starts) == 3


async def test_connection_error_is_retried() -> None:
    clock = FakeClock()
    server = Recorder(clock, [httpx.ConnectError("refused"), ok()])

    async with make_client(clock, server) as client:
        await client.get_listing(1)

    assert len(server.starts) == 2


# --- Retry-After parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("7", 7.0),
        ("0", 0.0),
        (" 12 ", 12.0),
        ("Thu, 26 Nov 2026 12:00:20 GMT", 20.0),
        ("Thu, 26 Nov 2026 11:59:00 GMT", 0.0),  # a date in the past means "now"
        ("-5", None),
        ("soon", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header, now=FIXED_NOW) == expected
