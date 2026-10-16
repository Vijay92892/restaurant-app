"""Customer table sessions: QR + current PIN (or, where the restaurant allows it, a location
check) → a temporary, revocable session for one table.

The session row permanently fixes (restaurant_id, table_id); customer requests derive their
context from it and never from request parameters. The cookie carries a 256-bit random token;
only its SHA-256 is stored. Neither the PIN, the QR token nor a position is ever part of it,
and guests' positions are never stored (the session only records *how* it was opened).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import geo
from app.core.config import Settings
from app.core.errors import AppError, NotFound, RateLimited
from app.core.net import RequestMeta
from app.core.pin import PinCrypto
from app.core.rate_limit import Budget, Limits, RateLimiter
from app.core.security import hash_token, new_opaque_token
from app.core.usage import Metric, UsageCounters
from app.db.session import bind_tenant
from app.models import DiningTable, Restaurant, TableSession
from app.models.enums import (
    ActorType,
    RestaurantStatus,
    TableSessionAccess,
    TableSessionEndReason,
    TableSessionStatus,
)
from app.realtime import outbox
from app.repositories import resolvers
from app.schemas.common import QR_TOKEN_PATTERN
from app.services import audit

SESSION_COOKIE = "__Host-ts"
_LAST_SEEN_RESOLUTION = timedelta(seconds=60)


class InvalidTableAccess(AppError):
    status_code = 401
    code = "invalid_table_access"
    message = "Invalid table access code."


class NoTableSession(AppError):
    status_code = 401
    code = "table_session_required"
    message = "Please scan the table QR code and enter the table PIN."


class AccessMethodOff(AppError):
    """This restaurant doesn't accept this way in (its policy is public on the landing page)."""

    status_code = 403
    code = "access_method_off"
    message = "This way of opening the table isn't available here. Please ask a member of staff."


class LocationRefused(AppError):
    status_code = 403
    code = "location_refused"
    message = "We couldn't confirm that you are at the restaurant."


# What guests are told: enough to fix an honest problem, nothing about the distance.
_LOCATION_REFUSALS = {
    geo.Verdict.OUTSIDE: ("location_outside", "You don't seem to be at the restaurant."),
    geo.Verdict.INACCURATE: (
        "location_inaccurate",
        "Your phone isn't sure enough where it is. Try again in a minute, nearer a window.",
    ),
    geo.Verdict.STALE: ("location_stale", "That location was too old. Please try again."),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def location_accepted(restaurant: Restaurant) -> bool:
    return restaurant.location_ordering_enabled and restaurant.location_latitude is not None


def pin_accepted(restaurant: Restaurant) -> bool:
    """The PIN always works unless the restaurant uses location access *without* a PIN backup."""
    return not (location_accepted(restaurant) and not restaurant.pin_fallback_enabled)


@dataclass(frozen=True)
class TableContext:
    restaurant: Restaurant
    table: DiningTable


@dataclass(frozen=True)
class CustomerSession:
    session: TableSession
    restaurant: Restaurant
    table: DiningTable

    @property
    def restaurant_id(self) -> uuid.UUID:
        return self.session.restaurant_id

    @property
    def table_id(self) -> uuid.UUID:
        return self.session.table_id


async def _load_table_context(
    db: AsyncSession, restaurant_id: uuid.UUID, table_id: uuid.UUID
) -> TableContext | None:
    result = await db.execute(
        select(Restaurant, DiningTable)
        .join(DiningTable, DiningTable.restaurant_id == Restaurant.id)
        .where(
            Restaurant.id == restaurant_id,
            DiningTable.restaurant_id == restaurant_id,
            DiningTable.id == table_id,
        )
    )
    row = result.first()
    if row is None:
        return None
    restaurant, table = row
    if (
        restaurant.status != RestaurantStatus.ACTIVE
        or not table.is_active
        or table.deleted_at is not None
    ):
        return None
    return TableContext(restaurant=restaurant, table=table)


async def resolve_qr(db: AsyncSession, qr_token: str) -> TableContext:
    """Public table landing data. Unknown/revoked/disabled ⇒ 404 (tokens are unguessable)."""
    routed = await resolvers.resolve_qr_token(db, qr_token)
    if routed is None:
        raise NotFound("This QR code is not valid.")
    restaurant_id, table_id = routed
    await bind_tenant(db, restaurant_id)
    ctx = await _load_table_context(db, restaurant_id, table_id)
    if ctx is None:
        raise NotFound("This QR code is not valid.")
    return ctx


async def open_session(
    db: AsyncSession,
    crypto: PinCrypto,
    limiter: RateLimiter,
    meta: RequestMeta,
    qr_token: str,
    pin: str,
    *,
    usage: UsageCounters | None = None,
) -> tuple[str, CustomerSession]:
    """Verify QR + PIN and create a session. Every failure cause yields the same error.
    `usage` counts accepted, wrong and locked-out attempts per restaurant (pilot observation)."""
    # Imported here to avoid a module cycle (pin_service uses end_active_sessions).
    from app.services import pin_service

    ip = meta.ip
    await limiter.enforce(Limits.PIN_ATTEMPTS_PER_IP, ip)
    # Every attempt counts as a failure until the PIN checks out (see Budget): a burst of
    # simultaneous guesses cannot slip past the budgets.
    budget = Budget(limiter)
    await budget.take(Limits.PIN_FAILURES_PER_IP, ip)

    if not re.fullmatch(QR_TOKEN_PATTERN, qr_token):
        raise InvalidTableAccess()  # never sent to the database; counts as a failure
    routed = await resolvers.resolve_qr_token(db, qr_token)
    if routed is None:
        raise InvalidTableAccess()
    restaurant_id, table_id = routed
    await bind_tenant(db, restaurant_id)
    ctx = await _load_table_context(db, restaurant_id, table_id)
    current = await pin_service.current_version(db, restaurant_id)
    if ctx is None or current is None:
        raise InvalidTableAccess()
    if not pin_accepted(ctx.restaurant):
        raise AccessMethodOff()  # location only here; still counted as a failure

    try:
        await budget.take(Limits.PIN_FAILURES_PER_TABLE, f"{table_id}:{current.version}")
        decision = await budget.take(
            Limits.PIN_FAILURES_PER_RESTAURANT, f"{restaurant_id}:{current.version}"
        )
    except RateLimited:
        if usage is not None:
            usage.add(restaurant_id, Metric.PIN_LOCKED)
        raise

    version = await pin_service.verify(db, crypto, ctx.restaurant, pin)
    if version is None:
        if usage is not None:
            usage.add(restaurant_id, Metric.PIN_WRONG)
        if decision.count == Limits.PIN_FAILURES_PER_RESTAURANT.max:
            # Logged once per window; Phase 8 also pushes this to staff devices.
            await audit.record(
                db,
                restaurant_id=restaurant_id,
                action="pin.bruteforce_suspected",
                actor_type=ActorType.ANONYMOUS,
                entity_type="pin_version",
                entity_id=current.id,
                details={"pin_version": current.version, "failures": decision.count},
                meta=meta,
            )
            outbox.stage(
                db, restaurant_id, "pin.bruteforce_suspected", payload={"table_id": str(table_id)}
            )
            await db.commit()
        raise InvalidTableAccess()
    await budget.refund()  # the right PIN: not a failure

    token, customer = await _start_session(db, ctx, TableSessionAccess.PIN, pin_version=version)
    if usage is not None:
        usage.add(restaurant_id, Metric.PIN_ACCEPTED)
    return token, customer


async def open_location_session(
    db: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    meta: RequestMeta,
    qr_token: str,
    report: geo.PositionReport,
    *,
    usage: UsageCounters | None = None,
) -> tuple[str, CustomerSession]:
    """QR + a position report inside the restaurant's radius → a session, without the PIN.

    The report is the guest's own claim (see app.core.geo): it is checked for freshness,
    precision and distance, used at most once, and budgeted like PIN guesses. Unknown QR
    codes fail exactly like on the PIN path.
    """
    ip = meta.ip
    await limiter.enforce(Limits.LOCATION_ATTEMPTS_PER_IP, ip)
    budget = Budget(limiter)  # every attempt is a failure until the report checks out
    await budget.take(Limits.LOCATION_FAILURES_PER_IP, ip)

    if not re.fullmatch(QR_TOKEN_PATTERN, qr_token):
        raise InvalidTableAccess()
    routed = await resolvers.resolve_qr_token(db, qr_token)
    if routed is None:
        raise InvalidTableAccess()
    restaurant_id, table_id = routed
    await bind_tenant(db, restaurant_id)
    ctx = await _load_table_context(db, restaurant_id, table_id)
    if ctx is None:
        raise InvalidTableAccess()
    restaurant = ctx.restaurant
    if not location_accepted(restaurant):
        raise AccessMethodOff()
    await budget.take(Limits.LOCATION_FAILURES_PER_TABLE, str(table_id))

    assert restaurant.location_latitude is not None and restaurant.location_longitude is not None
    fence = geo.Geofence(
        restaurant.location_latitude, restaurant.location_longitude, restaurant.location_radius_m
    )
    verdict = geo.judge(
        report,
        fence,
        now=utcnow(),
        max_accuracy_m=settings.location_max_accuracy_m,
        max_age=timedelta(seconds=settings.location_max_age_seconds),
        clock_skew=timedelta(seconds=settings.location_clock_skew_seconds),
    )
    if verdict is not geo.Verdict.INSIDE:
        if usage is not None:
            usage.add(restaurant_id, Metric.LOCATION_REFUSED)
        code, message = _LOCATION_REFUSALS[verdict]
        raise LocationRefused(message, code=code)
    # The same report (sent again, by anyone) never opens a second session.
    fingerprint = (
        f"{table_id}|{report.latitude!r}|{report.longitude!r}|{report.accuracy_m!r}|"
        f"{report.captured_at.isoformat()}"
    )
    if not (await limiter.hit(Limits.LOCATION_REPORT_ONCE, fingerprint)).allowed:
        if usage is not None:
            usage.add(restaurant_id, Metric.LOCATION_REFUSED)
        raise LocationRefused("Please try again.", code="location_reused")
    await budget.refund()

    token, customer = await _start_session(db, ctx, TableSessionAccess.LOCATION, pin_version=None)
    if usage is not None:
        usage.add(restaurant_id, Metric.LOCATION_ACCEPTED)
    return token, customer


async def _start_session(
    db: AsyncSession, ctx: TableContext, access: TableSessionAccess, *, pin_version: int | None
) -> tuple[str, CustomerSession]:
    """A new session for this table: a fresh random token, only its hash stored."""
    now = utcnow()
    token = new_opaque_token()
    session = TableSession(
        id=uuid.uuid4(),
        restaurant_id=ctx.restaurant.id,
        table_id=ctx.table.id,
        token_hash=hash_token(token),
        pin_version=pin_version,
        access_method=access.value,
        status=TableSessionStatus.ACTIVE.value,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=ctx.restaurant.table_session_ttl_minutes),
    )
    db.add(session)
    await db.commit()
    return token, CustomerSession(session=session, restaurant=ctx.restaurant, table=ctx.table)


async def resolve_session(db: AsyncSession, token: str | None) -> CustomerSession:
    """Authenticate a customer request from its session cookie."""
    if not token or len(token) > 128:
        raise NoTableSession()
    token_hash = hash_token(token)
    restaurant_id = await resolvers.resolve_table_session(db, token_hash)
    if restaurant_id is None:
        raise NoTableSession()
    await bind_tenant(db, restaurant_id)
    result = await db.execute(
        select(TableSession).where(
            TableSession.restaurant_id == restaurant_id, TableSession.token_hash == token_hash
        )
    )
    session = result.scalar_one_or_none()
    now = utcnow()
    if session is None or session.status != TableSessionStatus.ACTIVE or session.expires_at <= now:
        raise NoTableSession()
    ctx = await _load_table_context(db, restaurant_id, session.table_id)
    if ctx is None:
        raise NoTableSession()
    if now - session.last_seen_at >= _LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        await db.commit()
    return CustomerSession(session=session, restaurant=ctx.restaurant, table=ctx.table)


async def session_matches_table(
    db: AsyncSession, token: str | None, restaurant_id: uuid.UUID, table_id: uuid.UUID
) -> bool:
    """Does this cookie hold a live session for exactly this table? (`db` is already bound
    to `restaurant_id`; a session for another restaurant simply doesn't match.)"""
    if not token or len(token) > 128:
        return False
    token_hash = hash_token(token)
    if await resolvers.resolve_table_session(db, token_hash) != restaurant_id:
        return False
    result = await db.execute(
        select(func.count())
        .select_from(TableSession)
        .where(
            TableSession.restaurant_id == restaurant_id,
            TableSession.table_id == table_id,
            TableSession.token_hash == token_hash,
            TableSession.status == TableSessionStatus.ACTIVE.value,
            TableSession.expires_at > utcnow(),
        )
    )
    return result.scalar_one() == 1


async def leave(db: AsyncSession, customer: CustomerSession) -> None:
    session = customer.session
    session.status = TableSessionStatus.CLOSED.value
    session.ended_at = utcnow()
    session.end_reason = TableSessionEndReason.CUSTOMER_LEFT.value
    await db.commit()


async def end_active_sessions(
    db: AsyncSession,
    restaurant_id: uuid.UUID,
    *,
    reason: TableSessionEndReason,
    actor_user_id: uuid.UUID | None,
    table_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """End every active session of a restaurant (or of one table). The caller commits."""
    now = now or utcnow()
    status = (
        TableSessionStatus.CLOSED
        if reason in (TableSessionEndReason.TABLE_CLOSED, TableSessionEndReason.CUSTOMER_LEFT)
        else TableSessionStatus.REVOKED
    )
    conditions = [
        TableSession.restaurant_id == restaurant_id,
        TableSession.status == TableSessionStatus.ACTIVE.value,
    ]
    if table_id is not None:
        conditions.append(TableSession.table_id == table_id)
    result = await db.execute(
        update(TableSession)
        .where(*conditions)
        .values(
            status=status.value,
            ended_at=now,
            end_reason=reason.value,
            ended_by_user_id=actor_user_id,
        )
        .returning(TableSession.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


async def active_session_counts(db: AsyncSession, restaurant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(TableSession.table_id, func.count())
        .where(
            TableSession.restaurant_id == restaurant_id,
            TableSession.status == TableSessionStatus.ACTIVE.value,
            TableSession.expires_at > utcnow(),
        )
        .group_by(TableSession.table_id)
    )
    return {table_id: count for table_id, count in result.all()}
