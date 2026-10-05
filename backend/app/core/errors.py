"""Error types and handlers producing the uniform envelope

    {"error": {"code": ..., "message": ...}, "request_id": ...}

Nothing internal (stack traces, SQL, paths, submitted values) is ever returned to clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"
    message: str = "Bad request."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.headers = headers
        # Safe, client-useful context (e.g. which menu item is sold out). Never internals.
        self.extra = extra
        super().__init__(self.message)


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required."


class InvalidCredentials(AppError):
    status_code = 401
    code = "invalid_credentials"
    message = "Invalid credentials."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class PasswordChangeRequired(AppError):
    status_code = 403
    code = "password_change_required"
    message = "You must change your password before continuing."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "Not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class InvalidInput(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request is invalid."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many attempts. Please wait and try again."

    def __init__(self, retry_after: int) -> None:
        super().__init__(headers={"Retry-After": str(max(1, int(retry_after)))})


_STATUS_CODES = {
    400: ("bad_request", "Bad request."),
    401: ("unauthorized", "Authentication required."),
    403: ("forbidden", "You do not have permission to perform this action."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "Method not allowed."),
    413: ("payload_too_large", "Request body is too large."),
    415: ("unsupported_media_type", "Unsupported media type."),
    429: ("rate_limited", "Too many requests."),
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if extra:
        body["error"].update(extra)
    body["request_id"] = _request_id(request)
    return JSONResponse(body, status_code=status_code, headers=headers)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(
        request, exc.status_code, exc.code, exc.message, exc.headers, extra=exc.extra
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, message = _STATUS_CODES.get(exc.status_code, ("error", "Request failed."))
    return error_response(request, exc.status_code, code, message, exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Report where and why, never the submitted value (it may be a password or an attack string).
    fields = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": str(err.get("msg", ""))[:200]}
        for err in exc.errors()[:20]
    ]
    return error_response(
        request, 422, "validation_error", "The request is invalid.", extra={"fields": fields}
    )


async def _database_error(request: Request, exc: Exception) -> JSONResponse:
    """Input the database refuses as data (SQLSTATE class 22: a NUL character, an out-of-range
    number...) is the client's mistake, so a 400 — and a safety net behind input validation."""
    assert isinstance(exc, DBAPIError)
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None) or ""
    if str(sqlstate).startswith("22"):
        logger.warning(
            "database rejected input (SQLSTATE %s) request_id=%s", sqlstate, _request_id(request)
        )
        return error_response(request, 400, "bad_request", "Bad request.")
    return await _unhandled(request, exc)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error request_id=%s", _request_id(request), exc_info=exc)
    return error_response(request, 500, "internal_error", "Something went wrong.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(DBAPIError, _database_error)
    app.add_exception_handler(Exception, _unhandled)
