"""Restaurant table-ordering PIN: creation, rotation, verification and scheduled rotation.

Exactly one `pin_versions` row per restaurant has `superseded_at IS NULL` (the current PIN,
enforced by a partial unique index). Only the current version ever verifies, so old PINs are
dead the moment a rotation commits. Rotations serialise on the restaurant row lock.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.net import RequestMeta
from app.core.pin import KEY_VERSION, PinCrypto, generate_pin, is_pin_shaped
from app.db.session import Database, bind_tenant
from app.models import PinVersion, Restaurant
from app.models.enums import ActorType, PinRotationReason, TableSessionEndReason
from app.realtime import outbox
from app.realtime.bus import EventBus
from app.repositories import resolvers
from app.services import audit, table_session_service

logger = logging.getLogger("app.pin")

# Arbitrary constant identifying the "PIN scheduler leader" advisory lock.
_SCHEDULER_LOCK_KEY = 0x50494E52  # "PINR"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PinInfo:
    pin: str
    version: int
    pin_length: int
    rotated_at: datetime
    rotation_minutes: int | None
    next_rotation_at: datetime | None


async def _lock_restaurant(db: AsyncSession, restaurant_id: uuid.UUID) -> Restaurant:
    result = await db.execute(
        select(Restaurant).where(Restaurant.id == restaurant_id).with_for_update()
    )
    return result.scalar_one()


async def current_version(db: AsyncSession, restaurant_id: uuid.UUID) -> PinVersion | None:
    result = await db.execute(
        select(PinVersion).where(
            PinVersion.restaurant_id == restaurant_id, PinVersion.superseded_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


def _new_version(
    crypto: PinCrypto,
    restaurant_id: uuid.UUID,
    version: int,
    pin: str,
    reason: PinRotationReason,
    actor_user_id: uuid.UUID | None,
    now: datetime,
) -> PinVersion:
    return PinVersion(
        id=uuid.uuid4(),
        restaurant_id=restaurant_id,
        version=version,
        pin_hmac=crypto.digest(restaurant_id, version, pin),
        pin_ciphertext=crypto.encrypt(restaurant_id, version, pin),
        key_version=KEY_VERSION,
        reason=reason.value,
        created_by_user_id=actor_user_id,
        created_at=now,
    )


async def create_initial_pin(
    db: AsyncSession, crypto: PinCrypto, restaurant: Restaurant, *, now: datetime | None = None
) -> PinVersion:
    pin = generate_pin(restaurant.pin_length)
    row = _new_version(
        crypto, restaurant.id, 1, pin, PinRotationReason.INITIAL, None, now or utcnow()
    )
    db.add(row)
    await db.flush()
    return row


async def ensure_current_pin(
    db: AsyncSession, crypto: PinCrypto, restaurant_id: uuid.UUID
) -> PinVersion:
    """Current PIN, creating version 1 if a restaurant somehow has none (self-healing)."""
    current = await current_version(db, restaurant_id)
    if current is not None:
        return current
    restaurant = await _lock_restaurant(db, restaurant_id)
    current = await current_version(db, restaurant_id)
    if current is None:
        current = await create_initial_pin(db, crypto, restaurant)
    return current


@dataclass(frozen=True)
class RotationResult:
    version: PinVersion
    sessions_invalidated: int


async def rotate(
    db: AsyncSession,
    crypto: PinCrypto,
    restaurant_id: uuid.UUID,
    *,
    reason: PinRotationReason,
    actor_user_id: uuid.UUID | None,
    invalidate_sessions: bool = False,
    meta: RequestMeta | None = None,
    now: datetime | None = None,
) -> RotationResult | None:
    """Replace the current PIN. Returns None if a scheduled rotation is no longer due
    (another worker already did it). The caller commits."""
    now = now or utcnow()
    restaurant = await _lock_restaurant(db, restaurant_id)
    current = await current_version(db, restaurant_id)

    if reason is PinRotationReason.SCHEDULED:
        interval = restaurant.pin_rotation_minutes
        if (
            interval is None
            or current is None
            or current.created_at + timedelta(minutes=interval) > now
        ):
            return None

    previous_pin = None
    next_number = 1
    if current is not None:
        next_number = current.version + 1
        previous = crypto.decrypt(restaurant_id, current.version, current.pin_ciphertext)
        if len(previous) == restaurant.pin_length:
            previous_pin = previous  # never re-issue the same PIN twice in a row
        current.superseded_at = now
        await db.flush()  # free the "one current PIN" slot before inserting the next

    new_pin = generate_pin(restaurant.pin_length, previous=previous_pin)
    row = _new_version(crypto, restaurant_id, next_number, new_pin, reason, actor_user_id, now)
    db.add(row)
    await db.flush()

    ended = 0
    if invalidate_sessions:
        ended = await table_session_service.end_active_sessions(
            db,
            restaurant_id,
            reason=TableSessionEndReason.PIN_ROTATED,
            actor_user_id=actor_user_id,
            now=now,
        )
    await audit.record(
        db,
        restaurant_id=restaurant_id,
        action="pin.rotated",
        actor_type=ActorType.STAFF if actor_user_id else ActorType.SYSTEM,
        actor_user_id=actor_user_id,
        entity_type="pin_version",
        entity_id=row.id,
        details={
            "pin_version": next_number,
            "reason": reason.value,
            "sessions_invalidated": ended,
        },
        meta=meta,
    )
    outbox.stage(db, restaurant_id, "pin.rotated", payload={"version": next_number})
    return RotationResult(version=row, sessions_invalidated=ended)


async def verify(
    db: AsyncSession, crypto: PinCrypto, restaurant: Restaurant, candidate: str
) -> int | None:
    """Return the current PIN version if `candidate` matches it, else None."""
    if not is_pin_shaped(candidate, restaurant.pin_length):
        return None
    current = await current_version(db, restaurant.id)
    if current is None:
        return None
    if crypto.matches(restaurant.id, current.version, candidate, current.pin_hmac):
        return current.version
    return None


def describe(crypto: PinCrypto, restaurant: Restaurant, current: PinVersion) -> PinInfo:
    minutes = restaurant.pin_rotation_minutes
    return PinInfo(
        pin=crypto.decrypt(restaurant.id, current.version, current.pin_ciphertext),
        version=current.version,
        pin_length=restaurant.pin_length,
        rotated_at=current.created_at,
        rotation_minutes=minutes,
        next_rotation_at=current.created_at + timedelta(minutes=minutes) if minutes else None,
    )


async def change_settings(
    db: AsyncSession,
    crypto: PinCrypto,
    restaurant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    meta: RequestMeta,
    *,
    rotation_minutes: int | None,
    pin_length: int,
) -> None:
    """Update rotation interval / PIN length. A new length takes effect via an immediate
    rotation (the current PIN has the old length). The caller commits."""
    restaurant = await _lock_restaurant(db, restaurant_id)
    length_changed = restaurant.pin_length != pin_length
    if restaurant.pin_rotation_minutes == rotation_minutes and not length_changed:
        return
    restaurant.pin_rotation_minutes = rotation_minutes
    restaurant.pin_length = pin_length
    await db.flush()
    await audit.record(
        db,
        restaurant_id=restaurant_id,
        action="pin.settings_changed",
        actor_type=ActorType.STAFF,
        actor_user_id=actor_user_id,
        entity_type="restaurant",
        entity_id=restaurant_id,
        details={"rotation_minutes": rotation_minutes, "pin_length": pin_length},
        meta=meta,
    )
    if length_changed:
        await rotate(
            db,
            crypto,
            restaurant_id,
            reason=PinRotationReason.MANUAL,
            actor_user_id=actor_user_id,
            meta=meta,
        )


# --- scheduled rotation ------------------------------------------------------------------------


async def rotate_due_pins(
    db: Database, crypto: PinCrypto, *, now: datetime | None = None, bus: EventBus | None = None
) -> int:
    """Rotate every restaurant whose PIN is older than its interval. Safe to run on every
    worker: a transaction-scoped advisory lock elects one leader per tick, and `rotate`
    re-checks due-ness under the restaurant row lock."""
    now = now or utcnow()
    rotated = 0
    async with db.sessionmaker() as leader:
        got_lock = (
            await leader.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _SCHEDULER_LOCK_KEY}
            )
        ).scalar_one()
        if not got_lock:
            return 0
        due = await resolvers.due_pin_rotations(leader, now)
        for restaurant_id in due:
            try:
                async with db.sessionmaker() as session:
                    await bind_tenant(session, restaurant_id)
                    result = await rotate(
                        session,
                        crypto,
                        restaurant_id,
                        reason=PinRotationReason.SCHEDULED,
                        actor_user_id=None,
                        now=now,
                    )
                    await session.commit()
                    rotated += result is not None
                    if bus is not None:
                        await outbox.publish_released(session, bus)
            except Exception:
                logger.exception("scheduled PIN rotation failed for restaurant %s", restaurant_id)
    return rotated


async def run_scheduler(
    db: Database, crypto: PinCrypto, interval_seconds: int, bus: EventBus | None = None
) -> None:
    while True:
        try:
            count = await rotate_due_pins(db, crypto, bus=bus)
            if count:
                logger.info("rotated %d table PIN(s)", count)
        except Exception:
            logger.exception("PIN scheduler tick failed")
        await asyncio.sleep(interval_seconds)
