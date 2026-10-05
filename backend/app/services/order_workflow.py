"""Staff-side order workflow: the order state machine, cancellations and their review.

    PLACED ─▶ ACCEPTED ─▶ PREPARING ─▶ READY ─▶ SERVED ─▶ COMPLETED        (+ CANCELLED)
       └──────────────────▶ (kitchen may start directly)

Each transition requires a specific permission (kitchen cooks, waiters serve), the version the
client last saw (optimistic concurrency: a stale tablet gets 409 instead of clobbering newer
state), and is recorded as an order event and an audit entry in the same transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Forbidden, NotFound
from app.core.net import RequestMeta
from app.core.permissions import Permission
from app.models import Order, OrderEvent
from app.models.enums import ActorType, CancelReasonCode, OrderStatus
from app.services import audit, order_service
from app.services.auth_service import StaffContext

S = OrderStatus
P = Permission

TRANSITIONS: dict[tuple[OrderStatus, OrderStatus], Permission] = {
    (S.PLACED, S.ACCEPTED): P.ORDERS_KITCHEN_TRANSITION,
    (S.PLACED, S.PREPARING): P.ORDERS_KITCHEN_TRANSITION,
    (S.ACCEPTED, S.PREPARING): P.ORDERS_KITCHEN_TRANSITION,
    (S.PREPARING, S.READY): P.ORDERS_KITCHEN_TRANSITION,
    (S.READY, S.SERVED): P.ORDERS_SERVE,
    (S.SERVED, S.COMPLETED): P.ORDERS_SERVE,
}
TERMINAL = frozenset({S.COMPLETED, S.CANCELLED})
_TIMESTAMP = {
    S.ACCEPTED: "accepted_at",
    S.PREPARING: "preparing_at",
    S.READY: "ready_at",
    S.SERVED: "served_at",
    S.COMPLETED: "completed_at",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class VersionConflict(AppError):
    status_code = 409
    code = "version_conflict"
    message = "This order was just updated by someone else. Please refresh."


class InvalidTransition(AppError):
    status_code = 409
    code = "invalid_transition"
    message = "That change isn't possible for this order right now."


class CancelRequiresApproval(AppError):
    status_code = 403
    code = "cancel_requires_approval"
    message = (
        "This order is already being prepared. Request a cancellation for a manager to approve."
    )


async def lock_order(db: AsyncSession, restaurant_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    order = (
        await db.execute(
            select(Order)
            .where(Order.restaurant_id == restaurant_id, Order.id == order_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFound()
    return order


def _check_version(order: Order, expected_version: int | None) -> None:
    if expected_version is not None and order.version != expected_version:
        raise VersionConflict(extra={"current_version": order.version, "status": order.status})


async def _record(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta | None,
    order: Order,
    *,
    event_type: str,
    action: str,
    from_status: str,
    now: datetime,
    details: dict[str, Any] | None = None,
) -> None:
    order.version += 1
    order.updated_at = now
    db.add(
        OrderEvent(
            id=uuid.uuid4(),
            restaurant_id=order.restaurant_id,
            order_id=order.id,
            type=event_type,
            from_status=from_status,
            to_status=order.status,
            order_version=order.version,
            actor_type=ActorType.STAFF.value,
            actor_user_id=ctx.user_id,
            created_at=now,
            payload={},
        )
    )
    await audit.record(
        db,
        restaurant_id=order.restaurant_id,
        action=action,
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="order",
        entity_id=order.id,
        details={
            "order_number": order.order_number,
            "from_status": from_status,
            "to_status": order.status,
            **(details or {}),
        },
        meta=meta,
    )
    await db.flush()
    await order_service.stage_order_event(db, order, "order.updated")


def _apply_status(order: Order, to_status: OrderStatus, now: datetime) -> None:
    if to_status is S.PREPARING and order.accepted_at is None:
        order.accepted_at = now  # starting directly implies acceptance
    order.status = to_status.value
    setattr(order, _TIMESTAMP[to_status], now)


async def transition(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    order_id: uuid.UUID,
    to_status: OrderStatus,
    expected_version: int,
) -> Order:
    order = await lock_order(db, ctx.restaurant_id, order_id)
    _check_version(order, expected_version)
    current = S(order.status)
    required = TRANSITIONS.get((current, to_status))
    if required is None:
        raise InvalidTransition(
            f"An order that is {current.value} can't be marked {to_status.value}.",
            extra={"status": current.value},
        )
    if required not in ctx.permissions:
        raise Forbidden()
    now = utcnow()
    _apply_status(order, to_status, now)
    await _record(
        db,
        ctx,
        meta,
        order,
        event_type="order.status_changed",
        action="order.status_changed",
        from_status=current.value,
        now=now,
    )
    await db.commit()
    return order


def _may_cancel_directly(ctx: StaffContext, order: Order) -> bool:
    status = S(order.status)
    if P.ORDERS_CANCEL_PRIVILEGED in ctx.permissions:
        return True
    if P.ORDERS_CANCEL in ctx.permissions:
        if status in (S.PLACED, S.ACCEPTED):
            return True
        return status is S.PREPARING and ctx.restaurant.waiter_can_cancel_preparing
    return False


async def _cancel(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    order: Order,
    reason_code: CancelReasonCode,
    reason: str | None,
    action_details: dict[str, Any] | None = None,
) -> None:
    now = utcnow()
    previous = order.status
    order.status = S.CANCELLED.value
    order.cancelled_at = now
    order.cancel_reason_code = reason_code.value
    order.cancel_reason = reason
    order.cancelled_by_user_id = ctx.user_id
    await _record(
        db,
        ctx,
        meta,
        order,
        event_type="order.cancelled",
        action="order.cancelled",
        from_status=previous,
        now=now,
        details={"reason_code": reason_code.value, **(action_details or {})},
    )


async def cancel(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    order_id: uuid.UUID,
    reason_code: CancelReasonCode,
    reason: str | None,
    expected_version: int,
) -> Order:
    order = await lock_order(db, ctx.restaurant_id, order_id)
    _check_version(order, expected_version)
    if S(order.status) in TERMINAL:
        raise InvalidTransition(
            f"This order is already {order.status}.", extra={"status": order.status}
        )
    if not _may_cancel_directly(ctx, order):
        if P.ORDERS_CANCEL in ctx.permissions or P.ORDERS_CANCEL_REQUEST in ctx.permissions:
            raise CancelRequiresApproval()
        raise Forbidden()
    await _cancel(db, ctx, meta, order, reason_code, reason)
    await db.commit()
    return order


async def request_cancellation(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, order_id: uuid.UUID, reason: str
) -> Order:
    """Kitchen "flag an issue" / waiter asks a manager. Does not stop the kitchen by itself."""
    order = await lock_order(db, ctx.restaurant_id, order_id)
    if S(order.status) in TERMINAL:
        raise InvalidTransition(
            f"This order is already {order.status}.", extra={"status": order.status}
        )
    if order.cancel_requested_at is not None:
        return order  # already pending: idempotent
    now = utcnow()
    order.cancel_requested_at = now
    order.cancel_requested_by = "staff"
    order.cancel_requested_by_user_id = ctx.user_id
    order.cancel_request_reason = reason
    await _record(
        db,
        ctx,
        meta,
        order,
        event_type="order.cancel_requested",
        action="order.cancel_requested",
        from_status=order.status,
        now=now,
    )
    await db.commit()
    return order


async def review_cancellation(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    order_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None,
) -> Order:
    order = await lock_order(db, ctx.restaurant_id, order_id)
    if order.cancel_requested_at is None:
        raise InvalidTransition(
            "There is no cancellation request for this order.", code="no_cancellation_request"
        )
    if S(order.status) in TERMINAL:
        raise InvalidTransition(
            f"This order is already {order.status}.", extra={"status": order.status}
        )
    if approve:
        code = (
            CancelReasonCode.CUSTOMER_CHANGED_MIND
            if order.cancel_requested_by == "customer"
            else CancelReasonCode.OTHER
        )
        await _cancel(
            db,
            ctx,
            meta,
            order,
            code,
            note or order.cancel_request_reason,
            {"approved_request": True},
        )
    else:
        now = utcnow()
        order.cancel_requested_at = None
        order.cancel_requested_by = None
        order.cancel_requested_by_user_id = None
        order.cancel_request_reason = None
        await _record(
            db,
            ctx,
            meta,
            order,
            event_type="order.cancel_request_rejected",
            action="order.cancel_request_rejected",
            from_status=order.status,
            now=now,
        )
    await db.commit()
    return order


async def complete_served_orders(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, table_id: uuid.UUID
) -> int:
    """When a table is closed, its served orders are complete (the caller commits)."""
    orders = (
        (
            await db.execute(
                select(Order)
                .where(
                    Order.restaurant_id == ctx.restaurant_id,
                    Order.table_id == table_id,
                    Order.status == S.SERVED.value,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for order in orders:
        _apply_status(order, S.COMPLETED, now)
        await _record(
            db,
            ctx,
            meta,
            order,
            event_type="order.status_changed",
            action="order.status_changed",
            from_status=S.SERVED.value,
            now=now,
            details={"via": "table_closed"},
        )
    return len(orders)
