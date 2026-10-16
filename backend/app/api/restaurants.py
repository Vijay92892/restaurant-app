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
from app.core.config import Settings
from app.core.permissions import Permission
from app.models import AuditLog, Restaurant
from app.schemas.auth import TokenPairResponse
from app.schemas.common import patch_changes
from app.schemas.restaurant import (
    AuditLogResponse,
    CustomerAccessRequest,
    CustomerAccessResponse,
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


def _access(restaurant: Restaurant, settings: Settings) -> CustomerAccessResponse:
    return CustomerAccessResponse(
        location_ordering_enabled=restaurant.location_ordering_enabled,
        latitude=restaurant.location_latitude,
        longitude=restaurant.location_longitude,
        radius_m=restaurant.location_radius_m,
        pin_fallback_enabled=restaurant.pin_fallback_enabled,
        max_accuracy_m=settings.location_max_accuracy_m,
    )


# Owner and managers only (restaurant.settings.write), for reading too: the branch's point and
# the policy are theirs to decide; waiters and the kitchen never see this screen.
@router.get("/restaurant/customer-access", response_model=CustomerAccessResponse)
async def get_customer_access(
    settings: SettingsDep,
    ctx: StaffContext = require(Permission.RESTAURANT_SETTINGS_WRITE),
) -> CustomerAccessResponse:
    return _access(ctx.restaurant, settings)


@router.put("/restaurant/customer-access", response_model=CustomerAccessResponse)
async def update_customer_access(
    body: CustomerAccessRequest,
    db: DbSession,
    meta: MetaDep,
    settings: SettingsDep,
    ctx: StaffContext = require(Permission.RESTAURANT_SETTINGS_WRITE),
) -> CustomerAccessResponse:
    restaurant = await restaurant_service.update_customer_access(
        db,
        ctx,
        meta,
        location_ordering_enabled=body.location_ordering_enabled,
        latitude=body.latitude,
        longitude=body.longitude,
        radius_m=body.radius_m,
        pin_fallback_enabled=body.pin_fallback_enabled,
    )
    return _access(restaurant, settings)


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
