from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.auth import token_response
from app.api.deps import (
    CryptoDep,
    DbSession,
    LimiterDep,
    MetaDep,
    SettingsDep,
    get_auth_service,
    require,
)
from app.core.permissions import Permission
from app.models import AuditLog
from app.schemas.auth import TokenPairResponse
from app.schemas.common import patch_changes
from app.schemas.restaurant import (
    AuditLogResponse,
    RegisterRestaurantRequest,
    RestaurantResponse,
    UpdateRestaurantRequest,
)
from app.services import restaurant_service
from app.services.auth_service import AuthService, StaffContext

router = APIRouter(tags=["restaurant"])


@router.post("/restaurants", response_model=TokenPairResponse, status_code=status.HTTP_201_CREATED)
async def register_restaurant(
    body: RegisterRestaurantRequest,
    db: DbSession,
    settings: SettingsDep,
    crypto: CryptoDep,
    limiter: LimiterDep,
    meta: MetaDep,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPairResponse:
    restaurant, owner = await restaurant_service.register_restaurant(
        db,
        settings,
        crypto,
        limiter,
        meta,
        invite_code=body.invite_code,
        code=body.restaurant_code,
        name=body.restaurant_name,
        owner_username=body.owner_username,
        owner_display_name=body.owner_display_name,
        owner_email=body.owner_email,
        owner_password=body.password,
    )
    return token_response(await auth.start_session(owner, restaurant))


@router.get("/restaurant", response_model=RestaurantResponse)
async def get_restaurant(
    ctx: StaffContext = require(Permission.RESTAURANT_SETTINGS_READ),
) -> RestaurantResponse:
    return RestaurantResponse.model_validate(ctx.restaurant)


@router.patch("/restaurant", response_model=RestaurantResponse)
async def update_restaurant(
    body: UpdateRestaurantRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.RESTAURANT_SETTINGS_WRITE),
) -> RestaurantResponse:
    restaurant = await restaurant_service.update_settings(db, ctx, meta, patch_changes(body))
    return RestaurantResponse.model_validate(restaurant)


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def list_audit_logs(
    db: DbSession,
    ctx: StaffContext = require(Permission.AUDIT_READ),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[AuditLogResponse]:
    query = (
        select(AuditLog)
        .where(AuditLog.restaurant_id == ctx.restaurant_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    rows = (await db.execute(query)).scalars()
    return [AuditLogResponse.model_validate(row) for row in rows]
