"""Request-scoped dependencies: DB session, settings, rate limiter, and staff authN/authZ.

Authorization lives here (not in ASGI middleware) so every route declares the permission it
needs in its signature, e.g. `ctx: StaffContext = require(Permission.AUDIT_READ)`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Forbidden, PasswordChangeRequired, Unauthorized
from app.core.mailer import Mailer
from app.core.net import RequestMeta, request_meta
from app.core.permissions import Permission
from app.core.pin import PinCrypto
from app.core.rate_limit import RateLimiter
from app.realtime import outbox
from app.schemas.orders import IDEMPOTENCY_KEY_PATTERN
from app.services import table_session_service
from app.services.auth_service import AuthService, StaffContext, authenticate_access_token
from app.services.table_session_service import SESSION_COOKIE, CustomerSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, shared by all dependencies. Writes commit explicitly in the
    service layer; anything uncommitted is rolled back when the session closes. Live events
    staged by committed work are published once the request's work is done."""
    async with request.app.state.db.sessionmaker() as session:
        try:
            yield session
        finally:
            await outbox.publish_released(session, request.app.state.bus)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_request_meta(request: Request) -> RequestMeta:
    return request_meta(request, request.app.state.settings.trusted_proxies)


def get_pin_crypto(request: Request) -> PinCrypto:
    return request.app.state.pin_crypto


DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
MetaDep = Annotated[RequestMeta, Depends(get_request_meta)]
CryptoDep = Annotated[PinCrypto, Depends(get_pin_crypto)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        pattern=IDEMPOTENCY_KEY_PATTERN,
        description="Client-generated id for this order attempt; retries reuse it.",
    ),
]


# --- customers (table-session cookie) ---------------------------------------------------------


def verify_public_origin(request: Request, settings: SettingsDep) -> None:
    """CSRF defence in depth for cookie-authenticated customer writes (the cookie is already
    SameSite=Strict): a browser-sent Origin must be one of our customer web origins."""
    origin = request.headers.get("origin")
    if (
        origin is not None
        and settings.public_web_origins
        and origin not in settings.public_web_origins
    ):
        raise Forbidden("Request origin not allowed.", code="origin_not_allowed")


async def current_customer(request: Request, db: DbSession) -> CustomerSession:
    return await table_session_service.resolve_session(db, request.cookies.get(SESSION_COOKIE))


CurrentCustomer = Annotated[CustomerSession, Depends(current_customer)]
PublicWrite = Depends(verify_public_origin)


def get_auth_service(
    db: DbSession, settings: SettingsDep, limiter: LimiterDep, meta: MetaDep
) -> AuthService:
    return AuthService(db, settings, limiter, meta)


_bearer = HTTPBearer(auto_error=False)


async def current_staff_allow_password_change(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> StaffContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized()
    ctx = await authenticate_access_token(db, settings, credentials.credentials)
    request.state.staff_user_id = str(ctx.user_id)
    return ctx


async def current_staff(
    ctx: Annotated[StaffContext, Depends(current_staff_allow_password_change)],
) -> StaffContext:
    if ctx.user.must_change_password:
        raise PasswordChangeRequired()
    return ctx


def require(*permissions: Permission) -> Any:
    """Dependency that authenticates the caller and checks every listed permission."""

    async def checker(ctx: Annotated[StaffContext, Depends(current_staff)]) -> StaffContext:
        if not all(p in ctx.permissions for p in permissions):
            raise Forbidden()
        return ctx

    return Depends(checker)


CurrentStaff = Annotated[StaffContext, Depends(current_staff)]
