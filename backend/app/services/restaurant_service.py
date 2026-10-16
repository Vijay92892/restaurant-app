"""Restaurant (tenant) creation, self-service registration and settings."""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, InvalidInput
from app.core.net import RequestMeta
from app.core.pin import PinCrypto
from app.core.rate_limit import Limits, RateLimiter
from app.core.security import passwords, validate_new_password
from app.db.errors import violated_constraint
from app.db.session import bind_tenant
from app.models import Restaurant, User
from app.models.enums import ActorType, Role
from app.schemas.common import RESTAURANT_CODE_RE, USERNAME_RE
from app.services import audit, pin_service
from app.services.auth_service import StaffContext


async def create_restaurant_with_owner(
    db: AsyncSession,
    crypto: PinCrypto,
    *,
    code: str,
    name: str,
    owner_username: str,
    owner_display_name: str,
    owner_email: str | None,
    owner_password: str,
    source: str = "operator",
    meta: RequestMeta | None = None,
) -> tuple[Restaurant, User]:
    """Create a tenant, its owner and its first table PIN in the caller's transaction
    (the caller commits). `db` must be a fresh session: it is bound to the new tenant."""
    if not RESTAURANT_CODE_RE.fullmatch(code):
        raise InvalidInput("Restaurant code must be 3-32 lowercase letters, digits or hyphens.")
    if not USERNAME_RE.fullmatch(owner_username):
        raise InvalidInput("Username must be 3-32 lowercase letters, digits, '.', '_' or '-'.")
    validate_new_password(owner_password, username=owner_username)
    password_hash = await passwords.hash(owner_password)

    restaurant_id = uuid.uuid4()
    await bind_tenant(db, restaurant_id)
    restaurant = Restaurant(id=restaurant_id, code=code, name=name)
    db.add(restaurant)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict(
            "That restaurant code is already taken.", code="restaurant_code_taken"
        ) from exc

    owner = User(
        id=uuid.uuid4(),
        restaurant_id=restaurant_id,
        username=owner_username,
        email=owner_email,
        display_name=owner_display_name,
        role=Role.OWNER.value,
        password_hash=password_hash,
    )
    db.add(owner)
    await db.flush()
    await pin_service.create_initial_pin(db, crypto, restaurant)
    await audit.record(
        db,
        restaurant_id=restaurant_id,
        action="restaurant.created",
        actor_type=ActorType.SYSTEM,
        entity_type="restaurant",
        entity_id=restaurant_id,
        details={"code": code, "owner_username": owner_username, "source": source},
        meta=meta,
    )
    return restaurant, owner


def _invite_code_valid(settings: Settings, supplied: str | None) -> bool:
    if not supplied:
        return False
    valid = False
    for code in settings.registration_invite_codes:  # compare against all: constant time
        valid |= hmac.compare_digest(code.get_secret_value().encode(), supplied.encode())
    return valid


async def register_restaurant(
    db: AsyncSession,
    settings: Settings,
    crypto: PinCrypto,
    limiter: RateLimiter,
    meta: RequestMeta,
    *,
    invite_code: str | None,
    **fields: Any,
) -> tuple[Restaurant, User]:
    await limiter.enforce(Limits.REGISTRATION_PER_IP, meta.ip)
    if settings.registration_mode == "closed":
        raise Forbidden("Restaurant registration is closed.", code="registration_closed")
    if settings.registration_mode == "invite" and not _invite_code_valid(settings, invite_code):
        raise Forbidden("Invalid invite code.", code="invalid_invite_code")
    restaurant, owner = await create_restaurant_with_owner(
        db, crypto, source="self_service", meta=meta, **fields
    )
    await db.commit()
    return restaurant, owner


async def update_customer_access(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    *,
    location_ordering_enabled: bool,
    latitude: float | None,
    longitude: float | None,
    radius_m: int,
    pin_fallback_enabled: bool,
) -> Restaurant:
    """Location-based table access (owner and managers). Turning it off stops new PIN-less
    sessions at once; sessions already open keep running until they expire or staff end them
    (so nobody's open order is cut off). Positions are rounded to 6 decimals (about 10 cm)."""
    restaurant = ctx.restaurant
    wanted = {
        "location_ordering_enabled": location_ordering_enabled,
        "location_latitude": None if latitude is None else round(latitude, 6),
        "location_longitude": None if longitude is None else round(longitude, 6),
        "location_radius_m": radius_m,
        "pin_fallback_enabled": pin_fallback_enabled,
    }
    changed = sorted(k for k, v in wanted.items() if getattr(restaurant, k) != v)
    for key in changed:
        setattr(restaurant, key, wanted[key])
    if changed:
        await db.flush()
        await audit.record(
            db,
            restaurant_id=restaurant.id,
            action="restaurant.customer_access_changed",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="restaurant",
            entity_id=restaurant.id,
            details={
                "fields": changed,
                "location_ordering_enabled": restaurant.location_ordering_enabled,
                "pin_fallback_enabled": restaurant.pin_fallback_enabled,
                "radius_m": restaurant.location_radius_m,
            },
            meta=meta,
        )
    await db.commit()
    return restaurant


async def update_settings(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, changes: dict[str, Any]
) -> Restaurant:
    restaurant = ctx.restaurant
    changed = {k: v for k, v in changes.items() if getattr(restaurant, k) != v}
    for key, value in changed.items():
        setattr(restaurant, key, value)
    try:
        await db.flush()
    except IntegrityError as exc:  # pragma: no cover - schema validation precedes this
        raise Conflict(code=violated_constraint(exc) or "conflict") from exc
    if changed:
        await audit.record(
            db,
            restaurant_id=restaurant.id,
            action="restaurant.settings_changed",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="restaurant",
            entity_id=restaurant.id,
            details={"fields": sorted(changed)},
            meta=meta,
        )
    await db.commit()
    return restaurant
