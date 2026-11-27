"""FastAPI app factory.

`create_app()` builds a fresh app instead of using a module-level global, so tests can
build one with fake settings and fake health checks (like rendering a React component
with test props). uvicorn calls it with no arguments: `uvicorn --factory
martom.api.app:create_app`.
"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from martom.api.admin import (
    ReviewIssueSource,
    check_admin_token,
    load_from_database,
    warn_if_admin_disabled,
)
from martom.api.admin import router as admin_router
from martom.api.health import HealthCheck, check_postgres, check_redis
from martom.api.health import router as health_router
from martom.log import request_id_var
from martom.settings import Settings

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# A client may send its own id (CloudFront doesn't; it sends X-Amz-Cf-Id). It is kept only
# if it is short and plain, so it can't inject odd characters into logs or headers. Any
# client can pick any id, so it helps correlate logs but is not proof of anything.
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
HEALTH_CHECK_TIMEOUT_SECONDS = 3.0


def create_app(
    settings: Settings | None = None,
    health_checks: Mapping[str, HealthCheck] | None = None,
    check_timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
    review_issue_source: ReviewIssueSource | None = None,
) -> FastAPI:
    settings = settings or Settings()
    # Raises for a short ADMIN_TOKEN, or none in production: the server doesn't start.
    check_admin_token(settings)
    warn_if_admin_disabled(settings)
    if health_checks is None:
        # partial(f, x) is a new function that calls f(x), like `() => f(x)` in JS.
        health_checks = {
            "postgres": partial(check_postgres, settings.database_url),
            "redis": partial(check_redis, settings.redis_url),
        }

    app = FastAPI(title="martom-ai")
    # app.state is a place for app-wide objects that route handlers read via request.app.
    app.state.settings = settings
    app.state.health_checks = dict(health_checks)
    app.state.health_check_timeout = check_timeout
    app.state.review_issue_source = review_issue_source or partial(
        load_from_database, settings.database_url
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    # Registered after CORS, so it wraps it: CORS preflight responses get an id too.
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log the traceback here, while the request id is still set, and answer with a
            # plain 500 that still carries the id (the details stay in the log).
            logger.exception("unhandled error", extra={"request_id": request_id})
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request finished",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        request_id_var.reset(token)
        return response

    app.include_router(health_router)
    app.include_router(admin_router)
    return app
