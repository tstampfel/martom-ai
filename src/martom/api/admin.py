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


ADMIN_TOKEN_MIN_LENGTH = 32


class AdminTokenError(ValueError):
    """ADMIN_TOKEN breaks one of the API's startup rules: the API must not start."""


class AdminTokenMissingError(AdminTokenError):
    """APP_ENV=production but ADMIN_TOKEN is unset."""


class AdminTokenTooShortError(AdminTokenError):
    """ADMIN_TOKEN is set but shorter than ADMIN_TOKEN_MIN_LENGTH (any APP_ENV)."""


def check_admin_token(settings: Settings) -> None:
    """Tomislav's rules (#11): a token shorter than 32 characters stops the API in every
    APP_ENV; an unset token stops it only in production (local and test may skip it).

    Only the API calls this (`create_app`, `martom api`), so ADMIN_TOKEN never stops
    `martom sync`, `martom extract` or the scripts (#108). The messages never include the
    value.
    """
    token = settings.admin_token
    if token is not None and len(token.get_secret_value()) < ADMIN_TOKEN_MIN_LENGTH:
        raise AdminTokenTooShortError(
            f"ADMIN_TOKEN must be at least {ADMIN_TOKEN_MIN_LENGTH} characters "
            "(generate one with `openssl rand -hex 32`)"
        )
    if token is None and settings.app_env == "production":
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
