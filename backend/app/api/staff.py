from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import DbSession, MetaDep, require
from app.core.permissions import Permission
from app.schemas.common import patch_changes
from app.schemas.staff import (
    CreateStaffRequest,
    ResetStaffPasswordRequest,
    StaffResponse,
    UpdateStaffRequest,
)
from app.services import staff_service
from app.services.auth_service import StaffContext

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("", response_model=list[StaffResponse])
async def list_staff(
    db: DbSession, ctx: StaffContext = require(Permission.STAFF_READ)
) -> list[StaffResponse]:
    users = await staff_service.list_staff(db, ctx.restaurant_id)
    return [StaffResponse.model_validate(u) for u in users]


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    body: CreateStaffRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.STAFF_MANAGE),
) -> StaffResponse:
    user = await staff_service.create_staff(
        db,
        ctx,
        meta,
        username=body.username,
        display_name=body.display_name,
        role=body.role,
        email=body.email,
        temporary_password=body.temporary_password,
    )
    return StaffResponse.model_validate(user)


@router.patch("/{user_id}", response_model=StaffResponse)
async def update_staff(
    user_id: uuid.UUID,
    body: UpdateStaffRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.STAFF_MANAGE),
) -> StaffResponse:
    changes = patch_changes(body, nullable=frozenset({"email"}))
    user = await staff_service.update_staff(db, ctx, meta, user_id, changes)
    return StaffResponse.model_validate(user)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: uuid.UUID,
    body: ResetStaffPasswordRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.STAFF_MANAGE),
) -> Response:
    await staff_service.reset_password(db, ctx, meta, user_id, body.temporary_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    user_id: uuid.UUID,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.STAFF_MANAGE),
) -> Response:
    await staff_service.delete_staff(db, ctx, meta, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
