"""GET /health: is the app able to reach Postgres and Redis?

The AWS load balancer calls this to decide whether a task gets traffic, so it answers
503 when either dependency is down. The JSON body says which one, but only "ok" or
"error": the reason (which can contain hostnames) goes to the log, not to the public.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import psycopg
import redis.asyncio as redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# A check is an async function that returns None when the dependency works and raises
# when it doesn't. The type is like `() => Promise<void>` in TypeScript.
HealthCheck = Callable[[], Awaitable[None]]

CONNECT_TIMEOUT_SECONDS = 2

router = APIRouter()


async def check_postgres(database_url: str) -> None:
    # `async with` closes the connection when the block ends, even after an error.
    async with await psycopg.AsyncConnection.connect(
        database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        await conn.execute("SELECT 1")


async def check_redis(redis_url: str) -> None:
    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _run_check(name: str, check: HealthCheck, timeout: float) -> bool:
    try:
        # asyncio.timeout cancels the check if it runs too long, so a hanging
        # dependency can't make the load balancer's health request hang too.
        async with asyncio.timeout(timeout):
            await check()
    except Exception:
        logger.warning("health check %s failed", name, exc_info=True)
        return False
    return True


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    checks: dict[str, HealthCheck] = request.app.state.health_checks
    timeout: float = request.app.state.health_check_timeout

    # gather runs all checks at the same time, like Promise.all.
    results = await asyncio.gather(
        *(_run_check(name, check, timeout) for name, check in checks.items())
    )
    statuses = {name: "ok" if ok else "error" for name, ok in zip(checks, results, strict=True)}
    healthy = all(results)
    return JSONResponse(
        {"status": "ok" if healthy else "error", "checks": statuses},
        status_code=200 if healthy else 503,
    )
