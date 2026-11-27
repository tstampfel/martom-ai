"""GET /admin/review-issues: Toni's open review issues as JSON, behind a bearer token.

The token comes from ADMIN_TOKEN (`Settings.admin_token`). In production the API refuses
to start without it. In local and test a missing token logs one warning at startup and the
endpoint answers 404, the same as a route that doesn't exist, so it can never be open by
accident. A missing or wrong token in the request answers 401. Only the
`Authorization: Bearer <token>` header is accepted (a token in the URL would end up in
access logs).
"""

import logging
import secrets
from collections.abc import Awaitable, Callable

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from martom.review.report import ReportIssue, load_open_issues, report_json
from martom.settings import Settings

logger = logging.getLogger(__name__)


class AdminTokenMissingError(ValueError):
    """APP_ENV=production but ADMIN_TOKEN is unset: the API must not start."""


def check_admin_token(settings: Settings) -> None:
    """Tomislav's rule (27 Nov): production needs ADMIN_TOKEN; local and test may skip it.

    Only the API calls this (`create_app`, `martom api`), so a missing token never stops
    `martom sync` or `martom extract`. A token that is set but too short is rejected
    earlier, by `Settings` itself, in every APP_ENV.
    """
    if settings.admin_token is None and settings.app_env == "production":
        raise AdminTokenMissingError(
            "ADMIN_TOKEN is not set; APP_ENV=production needs it for the /admin endpoints "
            "(generate one with `openssl rand -hex 32`)"
        )


def warn_if_admin_disabled(settings: Settings) -> None:
    """Outside production, a missing token turns /admin off (404); say so once."""
    if settings.admin_token is None:
        logger.warning(
            "ADMIN_TOKEN is not set: the /admin endpoints are off (404)",
            extra={"app_env": settings.app_env},
        )


# Like `() => Promise<ReportIssue[]>` in TypeScript; tests pass a fake one.
ReviewIssueSource = Callable[[], Awaitable[list[ReportIssue]]]

router = APIRouter(prefix="/admin")
# auto_error=False: return None instead of raising for a missing header, so we choose
# the status codes ourselves (404 when disabled, 401 otherwise).
_bearer = HTTPBearer(auto_error=False)


async def load_from_database(database_url: str) -> list[ReportIssue]:
    """The default source: one short connection per request (Toni reads this rarely)."""
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as conn:
        return await load_open_issues(conn)


def _check_token(settings: Settings, credentials: HTTPAuthorizationCredentials | None) -> None:
    if settings.admin_token is None:
        raise HTTPException(status_code=404, detail="Not Found")
    expected = settings.admin_token.get_secret_value().encode()
    given = credentials.credentials.encode() if credentials else b""
    # compare_digest takes the same time wherever the first difference is, so response
    # timing doesn't reveal how much of a guessed token was right.
    if not secrets.compare_digest(given, expected):
        raise HTTPException(
            status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )


@router.get("/review-issues")
async def review_issues(request: Request) -> JSONResponse:
    credentials = await _bearer(request)
    _check_token(request.app.state.settings, credentials)
    source: ReviewIssueSource = request.app.state.review_issue_source
    issues = await source()
    # Listing text is data: JSON-encoded, never sniffed as HTML, and not cached anywhere.
    return JSONResponse(
        report_json(issues),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
