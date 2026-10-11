"""Staff authentication: login, token refresh/rotation, logout, password change and reset.

Invariants:
  * unknown restaurant / unknown user / inactive user / wrong password are indistinguishable
    to the caller (same error, same Argon2 cost);
  * the tenant is bound only from verified material (resolver result or signed token);
  * every authenticated request re-loads the user and session, so revocation, deactivation and
    role changes apply immediately rather than at token expiry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidCredentials, InvalidInput, Unauthorized
from app.core.mailer import OutgoingEmail
from app.core.net import UNKNOWN_IP, RequestMeta
from app.core.permissions import Permission, permissions_for
from app.core.rate_limit import Budget, Limits, RateLimiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_token,
    new_opaque_token,
    passwords,
    validate_new_password,
)
from app.db.session import bind_tenant
from app.models import AuthSession, PasswordResetToken, Restaurant, User
from app.models.enums import ActorType, RestaurantStatus, SessionRevokeReason
from app.repositories import auth_sessions, resolvers, users
from app.services import audit


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StaffContext:
    user: User
    restaurant: Restaurant
    session_id: uuid.UUID
    permissions: frozenset[Permission]
    # Plain copies taken at authentication: they stay readable even if a rollback later
    # expires the ORM objects (reading an expired attribute would need I/O).
    restaurant_id: uuid.UUID = field(init=False)
    user_id: uuid.UUID = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "restaurant_id", self.user.restaurant_id)
        object.__setattr__(self, "user_id", self.user.id)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    context: StaffContext


def _session_expired() -> Unauthorized:
    return Unauthorized("Your session has expired. Please sign in again.", code="session_expired")


def _invalid_reset_token() -> InvalidInput:
    return InvalidInput("This reset link is invalid or has expired.", code="invalid_reset_token")


async def authenticate_access_token(
    db: AsyncSession, settings: Settings, token: str
) -> StaffContext:
    claims = decode_access_token(settings, token)
    return await load_staff_context(db, claims.restaurant_id, claims.user_id, claims.session_id)


async def load_staff_context(
    db: AsyncSession, restaurant_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID
) -> StaffContext:
    """Re-load a verified principal; also used by long-lived WebSockets to re-check it."""
    # The ids come from a token signed by us, so it is safe to scope the session to them; the
    # user/session lookup below then runs under RLS for that tenant only.
    await bind_tenant(db, restaurant_id)
    principal = await auth_sessions.load_principal(db, restaurant_id, user_id, session_id)
    if principal is None:
        raise Unauthorized()
    user, session, restaurant = principal
    if (
        user.deleted_at is not None
        or not user.is_active
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
        or restaurant.status != RestaurantStatus.ACTIVE
    ):
        raise Unauthorized()
    return StaffContext(
        user=user,
        restaurant=restaurant,
        session_id=session.id,
        permissions=permissions_for(user.role),
    )


def login_account_key(restaurant_code: str, username: str) -> str:
    """Identifies an account in the wrong-password budget (LOGIN_FAILURES_PER_ACCOUNT)."""
    return f"{restaurant_code}\x00{username}"


class AuthService:
    def __init__(
        self, db: AsyncSession, settings: Settings, limiter: RateLimiter, meta: RequestMeta
    ) -> None:
        self.db = db
        self.settings = settings
        self.limiter = limiter
        self.meta = meta

    # --- login -------------------------------------------------------------------------------

    async def login(self, restaurant_code: str, username: str, password: str) -> IssuedTokens:
        ip = self.meta.ip
        account = login_account_key(restaurant_code, username)
        await self.limiter.enforce(Limits.LOGIN_PER_IP, ip)
        # Counted as a failure until the password checks out (see Budget), so simultaneous
        # guesses cannot exceed the per-account budget.
        budget = Budget(self.limiter)
        await budget.take(Limits.LOGIN_FAILURES_PER_ACCOUNT, account)
        await budget.take(Limits.LOGIN_FAILURES_PER_IP, ip)

        restaurant_id = await resolvers.resolve_restaurant_code(self.db, restaurant_code)
        user: User | None = None
        if restaurant_id is not None:
            await bind_tenant(self.db, restaurant_id)
            user = await users.get_by_username(self.db, restaurant_id, username)

        if user is None or not user.is_active:
            await passwords.burn(password)
            reason = "unknown_user" if user is None else "inactive_user"
            await self._login_failed(restaurant_id, user, username, reason, account)
            raise InvalidCredentials()

        matches, needs_rehash = await passwords.verify(user.password_hash, password)
        if not matches:
            await self._login_failed(restaurant_id, user, username, "bad_password", account)
            raise InvalidCredentials()

        assert restaurant_id is not None
        restaurant = await users.get_restaurant(self.db, restaurant_id)
        if restaurant is None:  # pragma: no cover - resolver guarantees existence
            raise InvalidCredentials()

        await budget.refund()
        if needs_rehash:
            user.password_hash = await passwords.hash(password)
        tokens = await self.start_session(user, restaurant)
        await self.limiter.reset(Limits.LOGIN_FAILURES_PER_ACCOUNT, account)
        return tokens

    async def start_session(self, user: User, restaurant: Restaurant) -> IssuedTokens:
        """Open a new login session for an already-authenticated user and commit."""
        now = utcnow()
        user.last_login_at = now
        session = await auth_sessions.create_session(
            self.db,
            restaurant_id=restaurant.id,
            user_id=user.id,
            now=now,
            expires_at=now + timedelta(days=self.settings.auth_session_absolute_days),
            user_agent=self.meta.user_agent,
            ip=self.meta.ip if self.meta.ip != UNKNOWN_IP else None,
        )
        tokens = await self._issue(user, restaurant, session, now, parent_id=None)
        await audit.record(
            self.db,
            restaurant_id=restaurant.id,
            action="auth.login.succeeded",
            actor_type=ActorType.STAFF,
            actor_user_id=user.id,
            entity_type="auth_session",
            entity_id=session.id,
            meta=self.meta,
        )
        await self.db.commit()
        return tokens

    async def _login_failed(
        self,
        restaurant_id: uuid.UUID | None,
        user: User | None,
        username: str,
        reason: str,
        account: str,
    ) -> None:
        # The failure was already counted when the attempt started.
        if restaurant_id is None:
            return  # no tenant to attribute the attempt to; rate limits still apply
        await audit.record(
            self.db,
            restaurant_id=restaurant_id,
            action="auth.login.failed",
            actor_type=ActorType.ANONYMOUS,
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            details={"username": username, "reason": reason},
            meta=self.meta,
        )
        await self.db.commit()

    # --- refresh -----------------------------------------------------------------------------

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        await self.limiter.enforce(Limits.REFRESH_PER_IP, self.meta.ip)
        token_hash = hash_token(refresh_token)
        restaurant_id = await resolvers.resolve_refresh_token(self.db, token_hash)
        if restaurant_id is None:
            raise _session_expired()
        await bind_tenant(self.db, restaurant_id)

        token = await auth_sessions.get_refresh_token_for_update(self.db, restaurant_id, token_hash)
        if token is None:
            raise _session_expired()
        session = await auth_sessions.get_session_for_update(
            self.db, restaurant_id, token.session_id
        )
        now = utcnow()
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now
            or token.revoked_at is not None
            or token.expires_at <= now
        ):
            raise _session_expired()

        user = await users.get_by_id(self.db, restaurant_id, session.user_id)
        restaurant = await users.get_restaurant(self.db, restaurant_id)
        if (
            user is None
            or not user.is_active
            or restaurant is None
            or restaurant.status != RestaurantStatus.ACTIVE
        ):
            raise _session_expired()

        if token.rotated_at is not None:
            await self._handle_reuse(token.id, token.rotated_at, session, now)
        else:
            token.rotated_at = now

        session.last_used_at = now
        tokens = await self._issue(user, restaurant, session, now, parent_id=token.id)
        await self.db.commit()
        return tokens

    async def _handle_reuse(
        self, token_id: uuid.UUID, rotated_at: datetime, session: AuthSession, now: datetime
    ) -> None:
        """An already-rotated refresh token was presented again.

        Within a short grace window, if the replacement was never used, this is almost certainly
        a lost response on a flaky network: supersede the unused replacement and continue.
        Otherwise assume the token was stolen and kill the whole session.
        """
        children = await auth_sessions.get_live_children_for_update(
            self.db, session.restaurant_id, token_id
        )
        in_grace = (now - rotated_at).total_seconds() <= self.settings.refresh_reuse_grace_seconds
        if in_grace and children and all(child.rotated_at is None for child in children):
            for child in children:
                child.revoked_at = now
            return

        await auth_sessions.revoke_session(
            self.db, session.restaurant_id, session.id, SessionRevokeReason.REFRESH_TOKEN_REUSE, now
        )
        await audit.record(
            self.db,
            restaurant_id=session.restaurant_id,
            action="auth.refresh.reuse_detected",
            actor_type=ActorType.ANONYMOUS,
            entity_type="auth_session",
            entity_id=session.id,
            details={"user_id": str(session.user_id)},
            meta=self.meta,
        )
        await self.db.commit()
        raise _session_expired()

    async def _issue(
        self,
        user: User,
        restaurant: Restaurant,
        session: AuthSession,
        now: datetime,
        *,
        parent_id: uuid.UUID | None,
    ) -> IssuedTokens:
        refresh_token = new_opaque_token()
        await auth_sessions.add_refresh_token(
            self.db,
            restaurant_id=user.restaurant_id,
            session_id=session.id,
            token_hash=hash_token(refresh_token),
            now=now,
            expires_at=min(
                now + timedelta(days=self.settings.refresh_token_idle_days), session.expires_at
            ),
            parent_id=parent_id,
        )
        access_token, _ = create_access_token(
            self.settings,
            user_id=user.id,
            restaurant_id=user.restaurant_id,
            session_id=session.id,
            now=now,
        )
        return IssuedTokens(
            access_token=access_token,
            expires_in=self.settings.access_token_ttl_seconds,
            refresh_token=refresh_token,
            context=StaffContext(
                user=user,
                restaurant=restaurant,
                session_id=session.id,
                permissions=permissions_for(user.role),
            ),
        )

    # --- logout ------------------------------------------------------------------------------

    async def logout(self, ctx: StaffContext) -> None:
        await auth_sessions.revoke_session(
            self.db, ctx.restaurant_id, ctx.session_id, SessionRevokeReason.LOGOUT, utcnow()
        )
        await audit.record(
            self.db,
            restaurant_id=ctx.restaurant_id,
            action="auth.logout",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="auth_session",
            entity_id=ctx.session_id,
            meta=self.meta,
        )
        await self.db.commit()

    async def logout_all(self, ctx: StaffContext) -> None:
        count = await auth_sessions.revoke_user_sessions(
            self.db, ctx.restaurant_id, ctx.user_id, SessionRevokeReason.LOGOUT_ALL, utcnow()
        )
        await audit.record(
            self.db,
            restaurant_id=ctx.restaurant_id,
            action="auth.logout_all",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            details={"sessions_revoked": count},
            meta=self.meta,
        )
        await self.db.commit()

    # --- password change ---------------------------------------------------------------------

    async def change_password(
        self, ctx: StaffContext, current_password: str, new_password: str
    ) -> None:
        user_key = str(ctx.user_id)
        budget = Budget(self.limiter)
        await budget.take(Limits.CHANGE_PASSWORD_FAILURES_PER_USER, user_key)
        matches, _ = await passwords.verify(ctx.user.password_hash, current_password)
        if not matches:
            raise InvalidInput("Current password is incorrect.", code="invalid_current_password")
        await budget.refund()
        validate_new_password(new_password, username=ctx.user.username)
        if new_password == current_password:
            raise InvalidInput("Choose a password you have not used here.", code="weak_password")

        now = utcnow()
        user = ctx.user  # loaded in this DB session by authenticate_access_token
        user.password_hash = await passwords.hash(new_password)
        user.password_changed_at = now
        user.must_change_password = False
        revoked = await auth_sessions.revoke_user_sessions(
            self.db,
            ctx.restaurant_id,
            ctx.user_id,
            SessionRevokeReason.PASSWORD_CHANGED,
            now,
            except_session_id=ctx.session_id,
        )
        await audit.record(
            self.db,
            restaurant_id=ctx.restaurant_id,
            action="auth.password.changed",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="user",
            entity_id=ctx.user_id,
            details={"other_sessions_revoked": revoked},
            meta=self.meta,
        )
        await self.db.commit()
        await self.limiter.reset(Limits.CHANGE_PASSWORD_FAILURES_PER_USER, user_key)

    # --- password reset ----------------------------------------------------------------------

    async def request_password_reset(
        self, restaurant_code: str, email: str
    ) -> OutgoingEmail | None:
        """Returns the email to send (if any). The caller always answers 202 and sends the email
        in the background, so neither the response nor its timing reveals whether it exists."""
        await self.limiter.enforce(Limits.RESET_REQUEST_PER_IP, self.meta.ip)
        await self.limiter.enforce(
            Limits.RESET_REQUEST_PER_ACCOUNT, f"{restaurant_code}\x00{email}"
        )
        restaurant_id = await resolvers.resolve_restaurant_code(self.db, restaurant_code)
        if restaurant_id is None:
            return None
        await bind_tenant(self.db, restaurant_id)
        user = await users.get_by_email(self.db, restaurant_id, email)
        if user is None or not user.is_active or not user.email:
            return None

        now = utcnow()
        await self.db.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.restaurant_id == restaurant_id,
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        token = new_opaque_token()
        self.db.add(
            PasswordResetToken(
                id=uuid.uuid4(),
                restaurant_id=restaurant_id,
                user_id=user.id,
                token_hash=hash_token(token),
                created_at=now,
                expires_at=now + timedelta(minutes=self.settings.password_reset_ttl_minutes),
            )
        )
        await audit.record(
            self.db,
            restaurant_id=restaurant_id,
            action="auth.password_reset.requested",
            actor_type=ActorType.ANONYMOUS,
            entity_type="user",
            entity_id=user.id,
            meta=self.meta,
        )
        await self.db.commit()

        # Token in the URL fragment: browsers never send fragments to servers or in Referer.
        link = f"{self.settings.password_reset_url}#token={token}"
        return OutgoingEmail(
            to=user.email,
            subject="Reset your password",
            body=(
                f"Hello {user.display_name},\n\n"
                f"Use this link within {self.settings.password_reset_ttl_minutes} minutes to "
                f"choose a new password:\n\n{link}\n\n"
                "If you did not ask for this, you can ignore this email."
            ),
        )

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        await self.limiter.enforce(Limits.RESET_CONFIRM_PER_IP, self.meta.ip)
        token_hash = hash_token(token)
        restaurant_id = await resolvers.resolve_password_reset_token(self.db, token_hash)
        if restaurant_id is None:
            raise _invalid_reset_token()
        await bind_tenant(self.db, restaurant_id)
        result = await self.db.execute(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.restaurant_id == restaurant_id,
                PasswordResetToken.token_hash == token_hash,
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        now = utcnow()
        if row is None or row.used_at is not None or row.expires_at <= now:
            raise _invalid_reset_token()
        user = await users.get_by_id(self.db, restaurant_id, row.user_id)
        if user is None or not user.is_active:
            raise _invalid_reset_token()
        # Validate before consuming the token, so a weak choice doesn't burn the link.
        validate_new_password(new_password, username=user.username)

        user.password_hash = await passwords.hash(new_password)
        user.password_changed_at = now
        user.must_change_password = False
        row.used_at = now
        await auth_sessions.revoke_user_sessions(
            self.db, restaurant_id, user.id, SessionRevokeReason.PASSWORD_RESET, now
        )
        await audit.record(
            self.db,
            restaurant_id=restaurant_id,
            action="auth.password_reset.completed",
            actor_type=ActorType.ANONYMOUS,
            entity_type="user",
            entity_id=user.id,
            meta=self.meta,
        )
        await self.db.commit()
