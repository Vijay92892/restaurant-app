"""Pure-ASGI middleware: request ids, security headers, request body size cap."""

from __future__ import annotations

import json
import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RequestIdMiddleware:
    """Accepts a well-formed inbound X-Request-ID (from our proxy) or generates one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        inbound = None
        for name, value in scope.get("headers", ()):
            if name == b"x-request-id":
                inbound = value.decode("latin-1")
                break
        request_id = (
            inbound if inbound and _VALID_REQUEST_ID.fullmatch(inbound) else uuid.uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


API_CSP = "default-src 'none'; frame-ancestors 'none'"
# Swagger UI (development only) loads its bundle from jsDelivr and boots with an inline script.
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    def __init__(
        self, app: ASGIApp, *, hsts: bool, docs_paths: frozenset[str] = frozenset()
    ) -> None:
        self.app = app
        self.docs_paths = docs_paths
        self.headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-site",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
        if hsts:
            self.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if scope.get("path") in self.docs_paths else API_CSP

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.headers.items():
                    headers.setdefault(key, value)
                headers.setdefault("Content-Security-Policy", csp)
                # API responses are per-user data: never cache by default.
                headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Rejects bodies over the limit, whether declared via Content-Length or streamed.
    `overrides` raise the limit for specific paths only (e.g. image uploads)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        overrides: tuple[tuple[re.Pattern[str], int], ...] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = overrides

    def _limit_for(self, path: str) -> int:
        for pattern, limit in self.overrides:
            if pattern.fullmatch(path):
                return limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = self._limit_for(scope.get("path", ""))

        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > max_bytes:
                    await _reject(scope, send, 413 if declared > max_bytes else 400)
                    return
                break

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    # FastAPI re-raises HTTPException unchanged → uniform 413 envelope.
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, limited_receive, send)


async def _reject(scope: Scope, send: Send, status: int) -> None:
    code, message = (
        ("payload_too_large", "Request body is too large.")
        if status == 413
        else ("bad_request", "Bad request.")
    )
    request_id = scope.get("state", {}).get("request_id")
    body = json.dumps(
        {"error": {"code": code, "message": message}, "request_id": request_id},
        separators=(",", ":"),
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
