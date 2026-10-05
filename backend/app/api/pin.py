from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CryptoDep, DbSession, MetaDep, require
from app.core.permissions import Permission
from app.models.enums import ActorType, PinRotationReason, TableSessionEndReason
from app.schemas.pin import (
    InvalidateSessionsResponse,
    PinResponse,
    PinSettingsRequest,
    RotatePinRequest,
    RotatePinResponse,
)
from app.services import audit, pin_service, table_session_service
from app.services.auth_service import StaffContext

router = APIRouter(tags=["pin"])


async def _current(db: DbSession, crypto: CryptoDep, ctx: StaffContext) -> PinResponse:
    current = await pin_service.ensure_current_pin(db, crypto, ctx.restaurant_id)
    await db.refresh(ctx.restaurant)
    info = pin_service.describe(crypto, ctx.restaurant, current)
    await db.commit()  # persists a self-healed initial PIN, if one had to be created
    return PinResponse(**info.__dict__)


@router.get("/pin", response_model=PinResponse)
async def get_pin(
    db: DbSession, crypto: CryptoDep, ctx: StaffContext = require(Permission.PIN_VIEW)
) -> PinResponse:
    return await _current(db, crypto, ctx)


@router.post("/pin/rotate", response_model=RotatePinResponse)
async def rotate_pin(
    body: RotatePinRequest,
    db: DbSession,
    crypto: CryptoDep,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.PIN_MANAGE),
) -> RotatePinResponse:
    result = await pin_service.rotate(
        db,
        crypto,
        ctx.restaurant_id,
        reason=PinRotationReason.MANUAL,
        actor_user_id=ctx.user_id,
        invalidate_sessions=body.invalidate_active_sessions,
        meta=meta,
    )
    assert result is not None  # manual rotations always happen
    await db.commit()
    info = pin_service.describe(crypto, ctx.restaurant, result.version)
    return RotatePinResponse(**info.__dict__, sessions_invalidated=result.sessions_invalidated)


@router.put("/pin/settings", response_model=PinResponse)
async def update_pin_settings(
    body: PinSettingsRequest,
    db: DbSession,
    crypto: CryptoDep,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.PIN_MANAGE),
) -> PinResponse:
    await pin_service.change_settings(
        db,
        crypto,
        ctx.restaurant_id,
        ctx.user_id,
        meta,
        rotation_minutes=body.rotation_minutes,
        pin_length=body.pin_length,
    )
    await db.commit()
    return await _current(db, crypto, ctx)


@router.post("/table-sessions/invalidate-all", response_model=InvalidateSessionsResponse)
async def invalidate_all_sessions(
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLE_SESSIONS_INVALIDATE_ALL),
) -> InvalidateSessionsResponse:
    count = await table_session_service.end_active_sessions(
        db,
        ctx.restaurant_id,
        reason=TableSessionEndReason.INVALIDATED_BY_STAFF,
        actor_user_id=ctx.user_id,
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="table_sessions.invalidated",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        details={"sessions_invalidated": count},
        meta=meta,
    )
    await db.commit()
    return InvalidateSessionsResponse(invalidated=count)
