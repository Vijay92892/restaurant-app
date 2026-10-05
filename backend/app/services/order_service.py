"""Order creation (customers and, from Phase 7, waiters) and customer order actions.

Never trust the client: restaurant and table come from the authenticated context; every menu
item and option is re-loaded and re-validated inside the restaurant (exists, live, in stock,
belongs to this dish, selection rules satisfied); names and display prices are snapshotted from
the database, never taken from the request. Creation is one transaction and idempotent per
(actor, Idempotency-Key).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, InvalidInput, NotFound
from app.core.net import RequestMeta
from app.db.errors import violated_constraint
from app.models import (
    DiningTable,
    MenuCategory,
    MenuItem,
    MenuItemModifierGroup,
    MenuModifier,
    MenuModifierGroup,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemModifier,
    Restaurant,
    User,
)
from app.models.enums import ActorType, CancelReasonCode, OrderSource, OrderStatus
from app.realtime import outbox
from app.schemas.orders import CreateOrderRequest, StaffOrderView
from app.services import audit

_IDEMPOTENCY_INDEXES = {"uq_orders_idempotency_session", "uq_orders_idempotency_user"}


def utcnow() -> datetime:
    return datetime.now(UTC)


class IdempotencyMismatch(AppError):
    status_code = 422
    code = "idempotency_mismatch"
    message = "This request key was already used for a different order."


class OrderTooLate(AppError):
    status_code = 409
    code = "order_too_late"
    message = "The kitchen has already started on this order. Please ask your waiter."


@dataclass(frozen=True)
class OrderActor:
    source: OrderSource
    table_session_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    @property
    def actor_type(self) -> ActorType:
        return ActorType.CUSTOMER if self.source is OrderSource.CUSTOMER else ActorType.STAFF


@dataclass(frozen=True)
class ValidatedLine:
    item: MenuItem
    quantity: int
    notes: str | None
    modifiers: list[tuple[MenuModifierGroup, MenuModifier]]


def fingerprint(request: CreateOrderRequest) -> bytes:
    """Stable hash of what was ordered, to tell a retry apart from a reused key."""
    canonical = {
        "items": [
            {
                "menu_item_id": str(line.menu_item_id),
                "quantity": line.quantity,
                "modifier_ids": sorted(str(m) for m in line.modifier_ids),
                "notes": line.notes or None,
            }
            for line in request.items
        ],
        "notes": request.notes or None,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


async def _find_existing(
    db: AsyncSession, restaurant_id: uuid.UUID, actor: OrderActor, key: str
) -> Order | None:
    query = select(Order).where(Order.restaurant_id == restaurant_id, Order.idempotency_key == key)
    if actor.table_session_id is not None:
        query = query.where(Order.table_session_id == actor.table_session_id)
    else:
        query = query.where(Order.placed_by_user_id == actor.user_id)
    return (await db.execute(query)).scalar_one_or_none()


def _replay(existing: Order, request_fingerprint: bytes) -> tuple[Order, bool]:
    if existing.request_fingerprint != request_fingerprint:
        raise IdempotencyMismatch()
    return existing, False


def _not_on_menu(item_id: uuid.UUID) -> InvalidInput:
    return InvalidInput(
        "Some dishes are no longer on the menu. Please refresh the menu.",
        code="item_not_on_menu",
        extra={"menu_item_id": str(item_id)},
    )


async def _validate(
    db: AsyncSession, restaurant_id: uuid.UUID, request: CreateOrderRequest
) -> list[ValidatedLine]:
    rid = restaurant_id
    item_ids = {line.menu_item_id for line in request.items}
    items = {
        i.id: i
        for i in (
            await db.execute(
                select(MenuItem).where(MenuItem.restaurant_id == rid, MenuItem.id.in_(item_ids))
            )
        ).scalars()
    }
    categories = {
        c.id: c
        for c in (
            await db.execute(
                select(MenuCategory).where(
                    MenuCategory.restaurant_id == rid,
                    MenuCategory.id.in_({i.category_id for i in items.values()}),
                )
            )
        ).scalars()
    }
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for item_id, group_id in (
        await db.execute(
            select(MenuItemModifierGroup.menu_item_id, MenuItemModifierGroup.group_id)
            .where(
                MenuItemModifierGroup.restaurant_id == rid,
                MenuItemModifierGroup.menu_item_id.in_(item_ids),
            )
            .order_by(MenuItemModifierGroup.sort_order)
        )
    ).all():
        links[item_id].append(group_id)
    groups = {
        g.id: g
        for g in (
            await db.execute(
                select(MenuModifierGroup).where(
                    MenuModifierGroup.restaurant_id == rid,
                    MenuModifierGroup.id.in_({g for gs in links.values() for g in gs}),
                    MenuModifierGroup.deleted_at.is_(None),
                )
            )
        ).scalars()
    }
    requested_modifiers = {m for line in request.items for m in line.modifier_ids}
    modifiers = {
        m.id: m
        for m in (
            await db.execute(
                select(MenuModifier).where(
                    MenuModifier.restaurant_id == rid, MenuModifier.id.in_(requested_modifiers)
                )
            )
        ).scalars()
    }

    validated: list[ValidatedLine] = []
    for line in request.items:
        item = items.get(line.menu_item_id)
        category = categories.get(item.category_id) if item else None
        if (
            item is None
            or item.deleted_at is not None
            or not item.is_active
            or category is None
            or category.deleted_at is not None
            or not category.is_active
        ):
            raise _not_on_menu(line.menu_item_id)
        if not item.is_available:
            raise Conflict(
                f"Sorry, {item.name} is sold out.",
                code="item_unavailable",
                extra={"menu_item_id": str(item.id)},
            )
        item_groups = [groups[g] for g in links.get(item.id, []) if g in groups]
        allowed = {g.id for g in item_groups}
        chosen: dict[uuid.UUID, list[MenuModifier]] = defaultdict(list)
        for modifier_id in line.modifier_ids:
            modifier = modifiers.get(modifier_id)
            if (
                modifier is None
                or modifier.deleted_at is not None
                or modifier.group_id not in allowed
            ):
                raise InvalidInput(
                    f"One of the options chosen for {item.name} isn't offered any more.",
                    code="invalid_option",
                    extra={"menu_item_id": str(item.id)},
                )
            if not modifier.is_available:
                raise Conflict(
                    f"Sorry, {modifier.name} is not available right now.",
                    code="option_unavailable",
                    extra={"menu_item_id": str(item.id), "modifier_id": str(modifier.id)},
                )
            chosen[modifier.group_id].append(modifier)
        for group in item_groups:
            count = len(chosen[group.id])
            if not group.min_select <= count <= group.max_select:
                raise InvalidInput(
                    f"Please check your choice of {group.name} for {item.name}.",
                    code="invalid_choice",
                    extra={"menu_item_id": str(item.id), "group_id": str(group.id)},
                )
        validated.append(
            ValidatedLine(
                item=item,
                quantity=line.quantity,
                notes=line.notes or None,
                modifiers=[
                    (group, modifier)
                    for group in item_groups
                    for modifier in sorted(chosen[group.id], key=lambda m: (m.sort_order, m.name))
                ],
            )
        )
    return validated


async def create_order(
    db: AsyncSession,
    *,
    restaurant_id: uuid.UUID,
    table_id: uuid.UUID,
    actor: OrderActor,
    request: CreateOrderRequest,
    idempotency_key: str,
) -> tuple[Order, bool]:
    """Returns (order, created). A retried request returns the original order."""
    request_fingerprint = fingerprint(request)
    existing = await _find_existing(db, restaurant_id, actor, idempotency_key)
    if existing is not None:
        return _replay(existing, request_fingerprint)

    lines = await _validate(db, restaurant_id, request)
    # The number and the row go in a savepoint: if a simultaneous duplicate of this request wins
    # the race, only the savepoint is undone and everything else this request loaded stays valid.
    savepoint = await db.begin_nested()
    # Per-restaurant order number; the row lock also serialises concurrent orders briefly.
    number = (
        await db.execute(
            update(Restaurant)
            .where(Restaurant.id == restaurant_id)
            .values(order_seq=Restaurant.order_seq + 1)
            .returning(Restaurant.order_seq)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one()

    now = utcnow()
    order = Order(
        id=uuid.uuid4(),
        restaurant_id=restaurant_id,
        order_number=number,
        table_id=table_id,
        source=actor.source.value,
        table_session_id=actor.table_session_id,
        placed_by_user_id=actor.user_id,
        status=OrderStatus.PLACED.value,
        version=1,
        notes=request.notes or None,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError as exc:
        await savepoint.rollback()
        if violated_constraint(exc) not in _IDEMPOTENCY_INDEXES:
            raise
        # A concurrent duplicate of this request won the race: return its order.
        existing = await _find_existing(db, restaurant_id, actor, idempotency_key)
        if existing is None:  # pragma: no cover - the conflicting row must exist
            raise
        return _replay(existing, request_fingerprint)

    order_items: list[tuple[OrderItem, ValidatedLine]] = []
    for line_no, line in enumerate(lines, start=1):
        order_item = OrderItem(
            id=uuid.uuid4(),
            restaurant_id=restaurant_id,
            order_id=order.id,
            line_no=line_no,
            menu_item_id=line.item.id,
            item_name=line.item.name,
            quantity=line.quantity,
            notes=line.notes,
            display_price_paise=line.item.display_price_paise,
            created_at=now,
        )
        db.add(order_item)
        order_items.append((order_item, line))
    await db.flush()
    for order_item, line in order_items:
        for position, (group, modifier) in enumerate(line.modifiers):
            db.add(
                OrderItemModifier(
                    id=uuid.uuid4(),
                    restaurant_id=restaurant_id,
                    order_item_id=order_item.id,
                    modifier_id=modifier.id,
                    group_name=group.name,
                    modifier_name=modifier.name,
                    display_price_delta_paise=modifier.display_price_delta_paise,
                    position=position,
                )
            )
    db.add(
        OrderEvent(
            id=uuid.uuid4(),
            restaurant_id=restaurant_id,
            order_id=order.id,
            type="order.created",
            to_status=OrderStatus.PLACED.value,
            order_version=1,
            actor_type=actor.actor_type.value,
            actor_user_id=actor.user_id,
            actor_table_session_id=actor.table_session_id,
            created_at=now,
            payload={"source": actor.source.value},
        )
    )
    await db.flush()
    await stage_order_event(db, order, "order.created")
    await db.commit()
    return order, True


# --- reading -------------------------------------------------------------------------------------


async def order_views(
    db: AsyncSession, restaurant_id: uuid.UUID, orders: list[Order]
) -> list[dict[str, Any]]:
    if not orders:
        return []
    order_ids = [o.id for o in orders]
    items = list(
        (
            await db.execute(
                select(OrderItem)
                .where(OrderItem.restaurant_id == restaurant_id, OrderItem.order_id.in_(order_ids))
                .order_by(OrderItem.order_id, OrderItem.line_no)
            )
        ).scalars()
    )
    mods: dict[uuid.UUID, list[OrderItemModifier]] = defaultdict(list)
    for mod in (
        await db.execute(
            select(OrderItemModifier).where(
                OrderItemModifier.restaurant_id == restaurant_id,
                OrderItemModifier.order_item_id.in_([i.id for i in items]),
            )
        )
    ).scalars():
        mods[mod.order_item_id].append(mod)
    lines: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        lines[item.order_id].append(
            {
                "id": item.id,
                "menu_item_id": item.menu_item_id,
                "name": item.item_name,
                "quantity": item.quantity,
                "notes": item.notes,
                "modifiers": [
                    f"{m.group_name}: {m.modifier_name}"
                    for m in sorted(mods[item.id], key=lambda m: m.position)
                ],
            }
        )
    return [
        {
            "id": o.id,
            "order_number": o.order_number,
            "status": o.status,
            "version": o.version,
            "created_at": o.created_at,
            "notes": o.notes,
            "cancel_requested": o.cancel_requested_at is not None,
            "items": lines[o.id],
        }
        for o in orders
    ]


async def session_orders(
    db: AsyncSession, restaurant_id: uuid.UUID, table_session_id: uuid.UUID
) -> list[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.restaurant_id == restaurant_id, Order.table_session_id == table_session_id)
        .order_by(Order.created_at.desc())
        .limit(50)
    )
    return list(result.scalars())


# --- customer cancellation -----------------------------------------------------------------------


async def customer_cancel(
    db: AsyncSession,
    *,
    restaurant_id: uuid.UUID,
    table_session_id: uuid.UUID,
    order_id: uuid.UUID,
    reason: str | None,
    meta: RequestMeta,
) -> Order:
    """PLACED ⇒ cancelled immediately. ACCEPTED ⇒ a cancellation *request* for staff to review.
    Anything later is too late for the customer (ask the waiter)."""
    order = (
        await db.execute(
            select(Order)
            .where(
                Order.restaurant_id == restaurant_id,
                Order.id == order_id,
                Order.table_session_id == table_session_id,  # only this session's orders
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFound()
    now = utcnow()
    previous = order.status
    if order.status == OrderStatus.PLACED:
        order.status = OrderStatus.CANCELLED.value
        order.cancelled_at = now
        order.cancel_reason_code = CancelReasonCode.CUSTOMER_CHANGED_MIND.value
        order.cancel_reason = reason
        event_type, action = "order.cancelled", "order.cancelled"
    elif order.status == OrderStatus.ACCEPTED:
        if order.cancel_requested_at is not None:
            return order  # already requested: idempotent
        order.cancel_requested_at = now
        order.cancel_requested_by = "customer"
        order.cancel_request_reason = reason
        event_type, action = "order.cancel_requested", "order.cancel_requested"
    elif order.status == OrderStatus.CANCELLED:
        return order
    else:
        raise OrderTooLate()
    order.version += 1
    order.updated_at = now
    db.add(
        OrderEvent(
            id=uuid.uuid4(),
            restaurant_id=restaurant_id,
            order_id=order.id,
            type=event_type,
            from_status=previous,
            to_status=order.status,
            order_version=order.version,
            actor_type=ActorType.CUSTOMER.value,
            actor_table_session_id=table_session_id,
            created_at=now,
            payload={},
        )
    )
    await audit.record(
        db,
        restaurant_id=restaurant_id,
        action=action,
        actor_type=ActorType.CUSTOMER,
        actor_table_session_id=table_session_id,
        entity_type="order",
        entity_id=order.id,
        details={"order_number": order.order_number, "from_status": previous},
        meta=meta,
    )
    await stage_order_event(db, order, "order.updated")
    await db.commit()
    return order


async def stage_order_event(db: AsyncSession, order: Order, event_type: str) -> None:
    """Queue a live update for this restaurant's staff devices; sent only if the transaction
    commits. Carries the full staff view so screens update without another request."""
    views = await staff_order_views(db, order.restaurant_id, [order])
    outbox.stage(
        db,
        order.restaurant_id,
        event_type,
        payload={"order": StaffOrderView.model_validate(views[0]).model_dump(mode="json")},
        order_id=order.id,
        order_version=order.version,
    )


# --- staff views -----------------------------------------------------------------------------


async def staff_order_views(
    db: AsyncSession, restaurant_id: uuid.UUID, orders: list[Order]
) -> list[dict[str, Any]]:
    """Customer view plus what staff need: table, who placed it, cancellation details."""
    views = await order_views(db, restaurant_id, orders)
    if not orders:
        return views
    labels = dict(
        (
            await db.execute(
                select(DiningTable.id, DiningTable.label).where(
                    DiningTable.restaurant_id == restaurant_id,
                    DiningTable.id.in_({o.table_id for o in orders}),
                )
            )
        ).all()
    )
    staff_ids = {o.placed_by_user_id for o in orders if o.placed_by_user_id}
    names = (
        dict(
            (
                await db.execute(
                    select(User.id, User.display_name).where(
                        User.restaurant_id == restaurant_id, User.id.in_(staff_ids)
                    )
                )
            ).all()
        )
        if staff_ids
        else {}
    )
    for order, view in zip(orders, views, strict=True):
        view.update(
            table_id=order.table_id,
            table_label=labels.get(order.table_id, "?"),
            source=order.source,
            placed_by=names.get(order.placed_by_user_id) if order.placed_by_user_id else None,
            updated_at=order.updated_at,
            ready_at=order.ready_at,
            cancel_request_reason=order.cancel_request_reason,
            cancel_requested_by=order.cancel_requested_by,
            cancel_reason_code=order.cancel_reason_code,
            cancel_reason=order.cancel_reason,
        )
    return views


async def list_orders(
    db: AsyncSession,
    restaurant_id: uuid.UUID,
    *,
    statuses: list[str] | None = None,
    table_id: uuid.UUID | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> list[Order]:
    query = select(Order).where(Order.restaurant_id == restaurant_id)
    if statuses:
        query = query.where(Order.status.in_(statuses))
    if table_id is not None:
        query = query.where(Order.table_id == table_id)
    if before is not None:
        query = query.where(Order.created_at < before)
    result = await db.execute(query.order_by(Order.created_at.desc()).limit(limit))
    return list(result.scalars())


async def get_order(db: AsyncSession, restaurant_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    order = (
        await db.execute(
            select(Order).where(Order.restaurant_id == restaurant_id, Order.id == order_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFound()
    return order
