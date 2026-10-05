from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from app.api.deps import DbSession, MetaDep, SettingsDep, require
from app.core.config import Settings
from app.core.media import MediaStorage
from app.core.permissions import Permission
from app.models import MenuItem
from app.schemas.common import patch_changes
from app.schemas.menu import (
    AvailabilityRequest,
    CategoryResponse,
    CreateCategoryRequest,
    CreateItemRequest,
    CreateModifierGroupRequest,
    CreateModifierRequest,
    ItemModifierGroupsRequest,
    ItemResponse,
    ModifierGroupResponse,
    ModifierResponse,
    OrderingMenuResponse,
    StaffMenuResponse,
    UpdateCategoryRequest,
    UpdateItemRequest,
    UpdateModifierGroupRequest,
    UpdateModifierRequest,
)
from app.services import menu_service
from app.services.auth_service import StaffContext

router = APIRouter(prefix="/menu", tags=["menu"])

Reader = require(Permission.MENU_READ)
Manager = require(Permission.MENU_MANAGE)
Availability = require(Permission.MENU_AVAILABILITY)


def _storage(request: Request) -> MediaStorage:
    return request.app.state.media


async def _item_response(db: DbSession, settings: Settings, item: MenuItem) -> ItemResponse:
    group_ids = await menu_service.item_group_ids(db, item.restaurant_id, item.id)
    return ItemResponse(**menu_service.item_payload(settings, item, group_ids))


@router.get("", response_model=StaffMenuResponse)
async def get_menu(
    db: DbSession, settings: SettingsDep, ctx: StaffContext = Reader
) -> StaffMenuResponse:
    snapshot = await menu_service.load_menu(db, ctx.restaurant_id)
    return StaffMenuResponse.model_validate(menu_service.staff_view(settings, snapshot))


@router.get("/ordering", response_model=OrderingMenuResponse)
async def get_ordering_menu(
    db: DbSession, settings: SettingsDep, ctx: StaffContext = Reader
) -> OrderingMenuResponse:
    """Live dishes with `orderable` flags — what a waiter can put on an order right now."""
    snapshot = await menu_service.load_menu(db, ctx.restaurant_id)
    return OrderingMenuResponse.model_validate(menu_service.public_view(settings, snapshot))


# --- categories ------------------------------------------------------------------------------


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CreateCategoryRequest, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> CategoryResponse:
    category = await menu_service.create_category(db, ctx, meta, body.model_dump())
    return CategoryResponse.model_validate(category)


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    body: UpdateCategoryRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> CategoryResponse:
    changes = patch_changes(body, nullable=frozenset({"description"}))
    category = await menu_service.update_category(db, ctx, meta, category_id, changes)
    return CategoryResponse.model_validate(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> Response:
    await menu_service.delete_category(db, ctx, meta, category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- items -----------------------------------------------------------------------------------


@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: CreateItemRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ItemResponse:
    item = await menu_service.create_item(db, ctx, meta, body.model_dump())
    return await _item_response(db, settings, item)


@router.patch("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: uuid.UUID,
    body: UpdateItemRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ItemResponse:
    changes = patch_changes(
        body, nullable=frozenset({"description", "dietary", "display_price_paise"})
    )
    item = await menu_service.update_item(db, ctx, meta, item_id, changes)
    return await _item_response(db, settings, item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> Response:
    await menu_service.delete_item(db, ctx, meta, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/items/{item_id}/availability", response_model=ItemResponse)
async def set_item_availability(
    item_id: uuid.UUID,
    body: AvailabilityRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Availability,
) -> ItemResponse:
    item = await menu_service.set_item_availability(db, ctx, meta, item_id, body.is_available)
    return await _item_response(db, settings, item)


@router.put("/items/{item_id}/modifier-groups", response_model=ItemResponse)
async def set_item_modifier_groups(
    item_id: uuid.UUID,
    body: ItemModifierGroupsRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ItemResponse:
    item = await menu_service.set_item_modifier_groups(db, ctx, meta, item_id, body.group_ids)
    return await _item_response(db, settings, item)


@router.post("/items/{item_id}/image", response_model=ItemResponse)
async def upload_item_image(
    item_id: uuid.UUID,
    request: Request,
    image: Annotated[UploadFile, File(description="JPG, PNG or WebP photo")],
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ItemResponse:
    data = await image.read(settings.max_image_upload_bytes + 1)
    item = await menu_service.set_item_image(
        db,
        ctx,
        meta,
        settings,
        _storage(request),
        item_id,
        filename=image.filename,
        content_type=image.content_type,
        data=data,
    )
    return await _item_response(db, settings, item)


@router.delete("/items/{item_id}/image", response_model=ItemResponse)
async def remove_item_image(
    item_id: uuid.UUID,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ItemResponse:
    item = await menu_service.remove_item_image(db, ctx, meta, _storage(request), item_id)
    return await _item_response(db, settings, item)


# --- option groups & options -----------------------------------------------------------------


@router.post(
    "/modifier-groups", response_model=ModifierGroupResponse, status_code=status.HTTP_201_CREATED
)
async def create_group(
    body: CreateModifierGroupRequest, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> ModifierGroupResponse:
    group = await menu_service.create_group(db, ctx, meta, body.model_dump())
    return ModifierGroupResponse.model_validate(group, from_attributes=True)


@router.patch("/modifier-groups/{group_id}", response_model=ModifierGroupResponse)
async def update_group(
    group_id: uuid.UUID,
    body: UpdateModifierGroupRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ModifierGroupResponse:
    group = await menu_service.update_group(db, ctx, meta, group_id, patch_changes(body))
    modifiers = await menu_service.group_modifiers(db, ctx.restaurant_id, group.id)
    return ModifierGroupResponse(
        id=group.id,
        name=group.name,
        min_select=group.min_select,
        max_select=group.max_select,
        sort_order=group.sort_order,
        modifiers=[ModifierResponse.model_validate(m) for m in modifiers],
    )


@router.delete("/modifier-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> Response:
    await menu_service.delete_group(db, ctx, meta, group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/modifier-groups/{group_id}/modifiers",
    response_model=ModifierResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_modifier(
    group_id: uuid.UUID,
    body: CreateModifierRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ModifierResponse:
    modifier = await menu_service.create_modifier(db, ctx, meta, group_id, body.model_dump())
    return ModifierResponse.model_validate(modifier)


@router.patch("/modifiers/{modifier_id}", response_model=ModifierResponse)
async def update_modifier(
    modifier_id: uuid.UUID,
    body: UpdateModifierRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Manager,
) -> ModifierResponse:
    changes = patch_changes(body, nullable=frozenset({"display_price_delta_paise"}))
    modifier = await menu_service.update_modifier(db, ctx, meta, modifier_id, changes)
    return ModifierResponse.model_validate(modifier)


@router.delete("/modifiers/{modifier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_modifier(
    modifier_id: uuid.UUID, db: DbSession, meta: MetaDep, ctx: StaffContext = Manager
) -> Response:
    await menu_service.delete_modifier(db, ctx, meta, modifier_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/modifiers/{modifier_id}/availability", response_model=ModifierResponse)
async def set_modifier_availability(
    modifier_id: uuid.UUID,
    body: AvailabilityRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Availability,
) -> ModifierResponse:
    modifier = await menu_service.set_modifier_availability(
        db, ctx, meta, modifier_id, body.is_available
    )
    return ModifierResponse.model_validate(modifier)
