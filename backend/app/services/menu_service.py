"""Menu management and read models.

Every lookup is filtered by tenant *and* runs under RLS. Ids supplied in request bodies
(category_id, group_ids) are re-resolved inside the caller's restaurant, so another
restaurant's ids are simply "unknown". Nothing is hard-deleted that past orders reference.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Conflict, InvalidInput, NotFound
from app.core.media import (
    ImageTooLarge,
    MediaStorage,
    check_declared,
    new_item_image_keys,
    process_image_async,
    small_key,
)
from app.core.net import RequestMeta
from app.db.errors import violated_constraint
from app.models import (
    MenuCategory,
    MenuItem,
    MenuItemModifierGroup,
    MenuModifier,
    MenuModifierGroup,
)
from app.models.enums import ActorType
from app.realtime import outbox
from app.services import audit
from app.services.auth_service import StaffContext

MAX_CATEGORIES = 100
MAX_ITEMS = 1000
MAX_GROUPS = 200
MAX_MODIFIERS_PER_GROUP = 50


def utcnow() -> datetime:
    return datetime.now(UTC)


async def _audit(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action=action,
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        meta=meta,
    )


def _blank_to_none(changes: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        if changes.get(key) == "":
            changes[key] = None
    return changes


async def _count(db: AsyncSession, model: Any, *conditions: Any) -> int:
    return (
        await db.execute(select(func.count()).select_from(model).where(*conditions))
    ).scalar_one()


async def _with_sort_order(
    db: AsyncSession, data: dict[str, Any], model: Any, *siblings: Any
) -> dict[str, Any]:
    """An omitted sort_order places the new entry after its existing siblings, so the menu
    keeps the order in which the owner created things (Mild, Medium, Hot — not alphabetical)."""
    if data.get("sort_order") is None:
        highest = (
            await db.execute(
                select(func.max(model.sort_order)).where(*siblings, model.deleted_at.is_(None))
            )
        ).scalar_one()
        data = {**data, "sort_order": min((highest if highest is not None else -1) + 1, 100000)}
    return data


async def _flush(db: AsyncSession, conflicts: dict[str, Conflict]) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        conflict = conflicts.get(violated_constraint(exc) or "")
        if conflict is not None:
            raise conflict from exc
        raise


_CATEGORY_NAME_TAKEN = {
    "uq_menu_categories_name": Conflict(
        "A category with this name already exists.", code="category_name_taken"
    )
}
_OPTION_NAME_TAKEN = {
    "uq_menu_modifiers_name": Conflict(
        "This group already has an option with that name.", code="modifier_name_taken"
    )
}


# --- lookups -------------------------------------------------------------------------------------


async def _get(db: AsyncSession, model: Any, restaurant_id: uuid.UUID, entity_id: uuid.UUID) -> Any:
    result = await db.execute(
        select(model).where(
            model.restaurant_id == restaurant_id,
            model.id == entity_id,
            model.deleted_at.is_(None),
        )
    )
    entity = result.scalar_one_or_none()
    if entity is None:
        raise NotFound()
    return entity


async def _category_for_body(
    db: AsyncSession, restaurant_id: uuid.UUID, category_id: uuid.UUID
) -> MenuCategory:
    try:
        return await _get(db, MenuCategory, restaurant_id, category_id)
    except NotFound:
        raise InvalidInput("Unknown category.", code="unknown_category") from None


# --- categories ----------------------------------------------------------------------------------


async def create_category(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, data: dict[str, Any]
) -> MenuCategory:
    rid = ctx.restaurant_id
    if (
        await _count(
            db, MenuCategory, MenuCategory.restaurant_id == rid, MenuCategory.deleted_at.is_(None)
        )
        >= MAX_CATEGORIES
    ):
        raise Conflict("Category limit reached.", code="category_limit_reached")
    data = await _with_sort_order(db, data, MenuCategory, MenuCategory.restaurant_id == rid)
    category = MenuCategory(
        id=uuid.uuid4(), restaurant_id=rid, **_blank_to_none(data, "description")
    )
    db.add(category)
    await _flush(db, _CATEGORY_NAME_TAKEN)
    await _audit(
        db,
        ctx,
        meta,
        "menu.category_created",
        "menu_category",
        category.id,
        {"name": category.name},
    )
    await db.commit()
    return category


async def update_category(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    category_id: uuid.UUID,
    changes: dict[str, Any],
) -> MenuCategory:
    category = await _get(db, MenuCategory, ctx.restaurant_id, category_id)
    for key, value in _blank_to_none(changes, "description").items():
        setattr(category, key, value)
    await _flush(db, _CATEGORY_NAME_TAKEN)
    await _audit(
        db,
        ctx,
        meta,
        "menu.category_updated",
        "menu_category",
        category.id,
        {"fields": sorted(changes)},
    )
    await db.commit()
    return category


async def delete_category(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, category_id: uuid.UUID
) -> None:
    category = await _get(db, MenuCategory, ctx.restaurant_id, category_id)
    if await _count(
        db,
        MenuItem,
        MenuItem.restaurant_id == ctx.restaurant_id,
        MenuItem.category_id == category.id,
        MenuItem.deleted_at.is_(None),
    ):
        raise Conflict("Move or delete this category's dishes first.", code="category_not_empty")
    category.deleted_at = utcnow()
    await _audit(
        db,
        ctx,
        meta,
        "menu.category_deleted",
        "menu_category",
        category.id,
        {"name": category.name},
    )
    await db.commit()


# --- items ---------------------------------------------------------------------------------------


async def create_item(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, data: dict[str, Any]
) -> MenuItem:
    rid = ctx.restaurant_id
    await _category_for_body(db, rid, data["category_id"])
    if (
        await _count(db, MenuItem, MenuItem.restaurant_id == rid, MenuItem.deleted_at.is_(None))
        >= MAX_ITEMS
    ):
        raise Conflict("Menu item limit reached.", code="item_limit_reached")
    data = await _with_sort_order(
        db,
        data,
        MenuItem,
        MenuItem.restaurant_id == rid,
        MenuItem.category_id == data["category_id"],
    )
    item = MenuItem(id=uuid.uuid4(), restaurant_id=rid, **_blank_to_none(data, "description"))
    db.add(item)
    await db.flush()
    await _audit(db, ctx, meta, "menu.item_created", "menu_item", item.id, {"name": item.name})
    await db.commit()
    return item


async def update_item(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    item_id: uuid.UUID,
    changes: dict[str, Any],
) -> MenuItem:
    item = await _get(db, MenuItem, ctx.restaurant_id, item_id)
    if "category_id" in changes:
        await _category_for_body(db, ctx.restaurant_id, changes["category_id"])
    for key, value in _blank_to_none(changes, "description").items():
        setattr(item, key, value)
    await db.flush()
    await _audit(
        db, ctx, meta, "menu.item_updated", "menu_item", item.id, {"fields": sorted(changes)}
    )
    await db.commit()
    return item


async def delete_item(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, item_id: uuid.UUID
) -> None:
    item = await _get(db, MenuItem, ctx.restaurant_id, item_id)
    item.deleted_at = utcnow()
    item.is_active = False
    await _audit(db, ctx, meta, "menu.item_deleted", "menu_item", item.id, {"name": item.name})
    await db.commit()


async def set_item_availability(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, item_id: uuid.UUID, available: bool
) -> MenuItem:
    item = await _get(db, MenuItem, ctx.restaurant_id, item_id)
    if item.is_available != available:
        item.is_available = available
        await _audit(
            db,
            ctx,
            meta,
            "menu.item_availability_changed",
            "menu_item",
            item.id,
            {"is_available": available},
        )
        outbox.stage(
            db,
            ctx.restaurant_id,
            "menu.availability_changed",
            payload={"menu_item_id": str(item.id), "is_available": available},
        )
        await db.commit()
    return item


async def set_item_modifier_groups(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    item_id: uuid.UUID,
    group_ids: list[uuid.UUID],
) -> MenuItem:
    rid = ctx.restaurant_id
    item = await _get(db, MenuItem, rid, item_id)
    if group_ids:
        found = set(
            (
                await db.execute(
                    select(MenuModifierGroup.id).where(
                        MenuModifierGroup.restaurant_id == rid,
                        MenuModifierGroup.id.in_(group_ids),
                        MenuModifierGroup.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        if found != set(group_ids):
            raise InvalidInput("Unknown option group.", code="unknown_modifier_group")
    await db.execute(
        delete(MenuItemModifierGroup).where(
            MenuItemModifierGroup.restaurant_id == rid,
            MenuItemModifierGroup.menu_item_id == item.id,
        )
    )
    for position, group_id in enumerate(group_ids):
        db.add(
            MenuItemModifierGroup(
                restaurant_id=rid, menu_item_id=item.id, group_id=group_id, sort_order=position
            )
        )
    await db.flush()
    await _audit(
        db,
        ctx,
        meta,
        "menu.item_groups_changed",
        "menu_item",
        item.id,
        {"group_ids": [str(g) for g in group_ids]},
    )
    await db.commit()
    return item


async def set_item_image(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    settings: Settings,
    storage: MediaStorage,
    item_id: uuid.UUID,
    *,
    filename: str | None,
    content_type: str | None,
    data: bytes,
) -> MenuItem:
    item = await _get(db, MenuItem, ctx.restaurant_id, item_id)
    check_declared(filename, content_type)
    if len(data) > settings.max_image_upload_bytes:
        raise ImageTooLarge()
    processed = await process_image_async(data)
    large_key, small = new_item_image_keys(ctx.restaurant_id)
    await storage.save(large_key, processed.large)
    await storage.save(small, processed.small)

    previous = item.image_path
    item.image_path = large_key
    try:
        await _audit(db, ctx, meta, "menu.item_image_changed", "menu_item", item.id)
        await db.commit()
    except BaseException:
        await storage.delete(large_key)
        await storage.delete(small)
        raise
    if previous:
        await storage.delete(previous)
        await storage.delete(small_key(previous))
    return item


async def remove_item_image(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    storage: MediaStorage,
    item_id: uuid.UUID,
) -> MenuItem:
    item = await _get(db, MenuItem, ctx.restaurant_id, item_id)
    previous = item.image_path
    if previous:
        item.image_path = None
        await _audit(db, ctx, meta, "menu.item_image_removed", "menu_item", item.id)
        await db.commit()
        await storage.delete(previous)
        await storage.delete(small_key(previous))
    return item


# --- modifier groups & options ---------------------------------------------------------------


async def create_group(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, data: dict[str, Any]
) -> MenuModifierGroup:
    rid = ctx.restaurant_id
    if (
        await _count(
            db,
            MenuModifierGroup,
            MenuModifierGroup.restaurant_id == rid,
            MenuModifierGroup.deleted_at.is_(None),
        )
        >= MAX_GROUPS
    ):
        raise Conflict("Option group limit reached.", code="group_limit_reached")
    data = await _with_sort_order(
        db, data, MenuModifierGroup, MenuModifierGroup.restaurant_id == rid
    )
    group = MenuModifierGroup(id=uuid.uuid4(), restaurant_id=rid, **data)
    db.add(group)
    await db.flush()
    await _audit(
        db, ctx, meta, "menu.group_created", "menu_modifier_group", group.id, {"name": group.name}
    )
    await db.commit()
    return group


async def update_group(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    group_id: uuid.UUID,
    changes: dict[str, Any],
) -> MenuModifierGroup:
    group = await _get(db, MenuModifierGroup, ctx.restaurant_id, group_id)
    min_select = changes.get("min_select", group.min_select)
    max_select = changes.get("max_select", group.max_select)
    if min_select > max_select:
        raise InvalidInput("min_select cannot exceed max_select.")
    for key, value in changes.items():
        setattr(group, key, value)
    await db.flush()
    await _audit(
        db,
        ctx,
        meta,
        "menu.group_updated",
        "menu_modifier_group",
        group.id,
        {"fields": sorted(changes)},
    )
    await db.commit()
    return group


async def delete_group(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, group_id: uuid.UUID
) -> None:
    group = await _get(db, MenuModifierGroup, ctx.restaurant_id, group_id)
    group.deleted_at = utcnow()
    await db.execute(
        delete(MenuItemModifierGroup).where(
            MenuItemModifierGroup.restaurant_id == ctx.restaurant_id,
            MenuItemModifierGroup.group_id == group.id,
        )
    )
    await _audit(
        db, ctx, meta, "menu.group_deleted", "menu_modifier_group", group.id, {"name": group.name}
    )
    await db.commit()


async def create_modifier(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    group_id: uuid.UUID,
    data: dict[str, Any],
) -> MenuModifier:
    rid = ctx.restaurant_id
    group = await _get(db, MenuModifierGroup, rid, group_id)
    if (
        await _count(
            db,
            MenuModifier,
            MenuModifier.restaurant_id == rid,
            MenuModifier.group_id == group.id,
            MenuModifier.deleted_at.is_(None),
        )
        >= MAX_MODIFIERS_PER_GROUP
    ):
        raise Conflict("This group has too many options.", code="modifier_limit_reached")
    data = await _with_sort_order(
        db, data, MenuModifier, MenuModifier.restaurant_id == rid, MenuModifier.group_id == group.id
    )
    modifier = MenuModifier(id=uuid.uuid4(), restaurant_id=rid, group_id=group.id, **data)
    db.add(modifier)
    await _flush(db, _OPTION_NAME_TAKEN)
    await _audit(
        db, ctx, meta, "menu.option_created", "menu_modifier", modifier.id, {"name": modifier.name}
    )
    await db.commit()
    return modifier


async def _get_modifier(
    db: AsyncSession, restaurant_id: uuid.UUID, modifier_id: uuid.UUID
) -> MenuModifier:
    modifier: MenuModifier = await _get(db, MenuModifier, restaurant_id, modifier_id)
    await _get(db, MenuModifierGroup, restaurant_id, modifier.group_id)  # group must be live too
    return modifier


async def update_modifier(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    modifier_id: uuid.UUID,
    changes: dict[str, Any],
) -> MenuModifier:
    modifier = await _get_modifier(db, ctx.restaurant_id, modifier_id)
    for key, value in changes.items():
        setattr(modifier, key, value)
    await _flush(db, _OPTION_NAME_TAKEN)
    await _audit(
        db,
        ctx,
        meta,
        "menu.option_updated",
        "menu_modifier",
        modifier.id,
        {"fields": sorted(changes)},
    )
    await db.commit()
    return modifier


async def delete_modifier(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, modifier_id: uuid.UUID
) -> None:
    modifier = await _get_modifier(db, ctx.restaurant_id, modifier_id)
    modifier.deleted_at = utcnow()
    await _audit(
        db, ctx, meta, "menu.option_deleted", "menu_modifier", modifier.id, {"name": modifier.name}
    )
    await db.commit()


async def set_modifier_availability(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, modifier_id: uuid.UUID, available: bool
) -> MenuModifier:
    modifier = await _get_modifier(db, ctx.restaurant_id, modifier_id)
    if modifier.is_available != available:
        modifier.is_available = available
        await _audit(
            db,
            ctx,
            meta,
            "menu.option_availability_changed",
            "menu_modifier",
            modifier.id,
            {"is_available": available},
        )
        outbox.stage(
            db,
            ctx.restaurant_id,
            "menu.availability_changed",
            payload={"modifier_id": str(modifier.id), "is_available": available},
        )
        await db.commit()
    return modifier


# --- read models ---------------------------------------------------------------------------------


@dataclass
class MenuSnapshot:
    categories: list[MenuCategory]
    items: list[MenuItem]
    groups: list[MenuModifierGroup]
    modifiers_by_group: dict[uuid.UUID, list[MenuModifier]] = field(default_factory=dict)
    groups_by_item: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)


async def load_menu(db: AsyncSession, restaurant_id: uuid.UUID) -> MenuSnapshot:
    """Every non-deleted menu entity of one restaurant, in display order (5 queries)."""

    async def rows(model: Any, *order: Any) -> list[Any]:
        result = await db.execute(
            select(model)
            .where(model.restaurant_id == restaurant_id, model.deleted_at.is_(None))
            .order_by(*order)
        )
        return list(result.scalars())

    categories = await rows(MenuCategory, MenuCategory.sort_order, func.lower(MenuCategory.name))
    items = await rows(MenuItem, MenuItem.sort_order, func.lower(MenuItem.name))
    groups = await rows(
        MenuModifierGroup, MenuModifierGroup.sort_order, func.lower(MenuModifierGroup.name)
    )
    modifiers = await rows(MenuModifier, MenuModifier.sort_order, func.lower(MenuModifier.name))
    links = (
        await db.execute(
            select(MenuItemModifierGroup.menu_item_id, MenuItemModifierGroup.group_id)
            .where(MenuItemModifierGroup.restaurant_id == restaurant_id)
            .order_by(MenuItemModifierGroup.menu_item_id, MenuItemModifierGroup.sort_order)
        )
    ).all()

    live_groups = {g.id for g in groups}
    snapshot = MenuSnapshot(categories=categories, items=items, groups=groups)
    by_group: dict[uuid.UUID, list[MenuModifier]] = defaultdict(list)
    for modifier in modifiers:
        by_group[modifier.group_id].append(modifier)
    snapshot.modifiers_by_group = dict(by_group)
    by_item: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for item_id, group_id in links:
        if group_id in live_groups:
            by_item[item_id].append(group_id)
    snapshot.groups_by_item = dict(by_item)
    return snapshot


async def item_group_ids(
    db: AsyncSession, restaurant_id: uuid.UUID, item_id: uuid.UUID
) -> list[uuid.UUID]:
    result = await db.execute(
        select(MenuItemModifierGroup.group_id)
        .join(
            MenuModifierGroup,
            (MenuModifierGroup.id == MenuItemModifierGroup.group_id)
            & (MenuModifierGroup.restaurant_id == MenuItemModifierGroup.restaurant_id),
        )
        .where(
            MenuItemModifierGroup.restaurant_id == restaurant_id,
            MenuItemModifierGroup.menu_item_id == item_id,
            MenuModifierGroup.deleted_at.is_(None),
        )
        .order_by(MenuItemModifierGroup.sort_order)
    )
    return list(result.scalars())


async def group_modifiers(
    db: AsyncSession, restaurant_id: uuid.UUID, group_id: uuid.UUID
) -> list[MenuModifier]:
    result = await db.execute(
        select(MenuModifier)
        .where(
            MenuModifier.restaurant_id == restaurant_id,
            MenuModifier.group_id == group_id,
            MenuModifier.deleted_at.is_(None),
        )
        .order_by(MenuModifier.sort_order, func.lower(MenuModifier.name))
    )
    return list(result.scalars())


def image_urls(settings: Settings, image_path: str | None) -> tuple[str | None, str | None]:
    if not image_path:
        return None, None
    prefix = settings.media_url_prefix
    return f"{prefix}/{image_path}", f"{prefix}/{small_key(image_path)}"


def group_is_satisfiable(group: MenuModifierGroup, options: list[MenuModifier]) -> bool:
    """Can a diner currently make a valid choice in this group?"""
    return sum(1 for o in options if o.is_available) >= group.min_select


def item_payload(settings: Settings, item: MenuItem, group_ids: list[uuid.UUID]) -> dict[str, Any]:
    image_url, thumbnail_url = image_urls(settings, item.image_path)
    return {
        "id": item.id,
        "category_id": item.category_id,
        "name": item.name,
        "description": item.description,
        "dietary": item.dietary,
        "display_price_paise": item.display_price_paise,
        "image_url": image_url,
        "thumbnail_url": thumbnail_url,
        "is_active": item.is_active,
        "is_available": item.is_available,
        "sort_order": item.sort_order,
        "modifier_group_ids": group_ids,
    }


def staff_view(settings: Settings, snapshot: MenuSnapshot) -> dict[str, Any]:
    items_by_category: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for item in snapshot.items:
        items_by_category[item.category_id].append(
            item_payload(settings, item, snapshot.groups_by_item.get(item.id, []))
        )
    return {
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "sort_order": c.sort_order,
                "is_active": c.is_active,
                "items": items_by_category.get(c.id, []),
            }
            for c in snapshot.categories
        ],
        "modifier_groups": [
            {
                "id": g.id,
                "name": g.name,
                "min_select": g.min_select,
                "max_select": g.max_select,
                "sort_order": g.sort_order,
                "modifiers": snapshot.modifiers_by_group.get(g.id, []),
            }
            for g in snapshot.groups
        ],
    }


def public_view(settings: Settings, snapshot: MenuSnapshot) -> dict[str, Any]:
    """What a diner may see: active categories and items only; sold-out items are shown but
    flagged; an item is orderable only if every required option group can still be satisfied."""
    satisfiable = {
        g.id: group_is_satisfiable(g, snapshot.modifiers_by_group.get(g.id, []))
        for g in snapshot.groups
    }
    active_categories = [c for c in snapshot.categories if c.is_active]
    active_ids = {c.id for c in active_categories}
    used_groups: set[uuid.UUID] = set()
    items_by_category: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for item in snapshot.items:
        if not item.is_active or item.category_id not in active_ids:
            continue
        group_ids = snapshot.groups_by_item.get(item.id, [])
        used_groups.update(group_ids)
        image_url, thumbnail_url = image_urls(settings, item.image_path)
        items_by_category[item.category_id].append(
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "dietary": item.dietary,
                "display_price_paise": item.display_price_paise,
                "image_url": image_url,
                "thumbnail_url": thumbnail_url,
                "is_available": item.is_available,
                "orderable": item.is_available and all(satisfiable[g] for g in group_ids),
                "modifier_group_ids": group_ids,
            }
        )
    # snapshot.groups is already in display order; keep only those some visible item uses.
    visible_groups = [g for g in snapshot.groups if g.id in used_groups]
    return {
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "items": items_by_category[c.id],
            }
            for c in active_categories
            if items_by_category.get(c.id)
        ],
        "modifier_groups": [
            {
                "id": g.id,
                "name": g.name,
                "min_select": g.min_select,
                "max_select": g.max_select,
                "options": [
                    {
                        "id": o.id,
                        "name": o.name,
                        "display_price_delta_paise": o.display_price_delta_paise,
                        "is_available": o.is_available,
                    }
                    for o in snapshot.modifiers_by_group.get(g.id, [])
                ],
            }
            for g in visible_groups
        ],
    }
