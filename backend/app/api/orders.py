"""Staff order API: list/inspect orders, place orders for a table (waiter, no PIN), move orders
through the state machine, cancel and review cancellation requests."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, IdempotencyKey, MetaDep, require
from app.core.errors import InvalidInput
from app.core.permissions import Permission
from app.models import Order
from app.models.enums import CancelReasonCode, OrderSource, OrderStatus
from app.schemas.orders import (
    CancellationRequestBody,
    ReviewCancellationRequest,
    StaffCancelRequest,
    StaffCreateOrderRequest,
    StaffOrderView,
    TransitionRequest,
)
from app.services import order_service, order_workflow, table_service
from app.services.auth_service import StaffContext
from app.services.order_service import OrderActor

router = APIRouter(prefix="/orders", tags=["orders"])

StatusName = Literal["placed", "accepted", "preparing", "ready", "served", "completed", "cancelled"]
Reader = require(Permission.ORDERS_READ)


async def _view(db: DbSession, ctx: StaffContext, order: Order) -> StaffOrderView:
    views = await order_service.staff_order_views(db, ctx.restaurant_id, [order])
    return StaffOrderView.model_validate(views[0])


@router.get("", response_model=list[StaffOrderView])
async def list_orders(
    db: DbSession,
    ctx: StaffContext = Reader,
    order_status: Annotated[list[StatusName] | None, Query(alias="status")] = None,
    table_id: uuid.UUID | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[StaffOrderView]:
    orders = await order_service.list_orders(
        db,
        ctx.restaurant_id,
        statuses=list(order_status) if order_status else None,
        table_id=table_id,
        before=before,
        limit=limit,
    )
    views = await order_service.staff_order_views(db, ctx.restaurant_id, orders)
    return [StaffOrderView.model_validate(v) for v in views]


@router.get("/{order_id}", response_model=StaffOrderView)
async def get_order(
    order_id: uuid.UUID, db: DbSession, ctx: StaffContext = Reader
) -> StaffOrderView:
    return await _view(db, ctx, await order_service.get_order(db, ctx.restaurant_id, order_id))


@router.post(
    "",
    response_model=StaffOrderView,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Retry of an earlier request: the original order"}},
)
async def place_order(
    body: StaffCreateOrderRequest,
    idempotency_key: IdempotencyKey,
    response: Response,
    db: DbSession,
    ctx: StaffContext = require(Permission.ORDERS_CREATE),
) -> StaffOrderView:
    table = await table_service.get_table(db, ctx.restaurant_id, body.table_id)
    if not table.is_active:
        raise InvalidInput("This table is switched off.", code="table_unavailable")
    restaurant_id, user_id = ctx.restaurant_id, ctx.user_id
    order, created = await order_service.create_order(
        db,
        restaurant_id=restaurant_id,
        table_id=table.id,
        actor=OrderActor(OrderSource.WAITER, user_id=user_id),
        request=body,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return await _view(db, ctx, order)


@router.post("/{order_id}/transition", response_model=StaffOrderView)
async def transition(
    order_id: uuid.UUID,
    body: TransitionRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Reader,
) -> StaffOrderView:
    order = await order_workflow.transition(
        db, ctx, meta, order_id, OrderStatus(body.to_status), body.expected_version
    )
    return await _view(db, ctx, order)


@router.post("/{order_id}/cancel", response_model=StaffOrderView)
async def cancel(
    order_id: uuid.UUID,
    body: StaffCancelRequest,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = Reader,
) -> StaffOrderView:
    order = await order_workflow.cancel(
        db,
        ctx,
        meta,
        order_id,
        CancelReasonCode(body.reason_code),
        body.reason or None,
        body.expected_version,
    )
    return await _view(db, ctx, order)


@router.post("/{order_id}/cancellation-request", response_model=StaffOrderView)
async def request_cancellation(
    order_id: uuid.UUID,
    body: CancellationRequestBody,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.ORDERS_CANCEL_REQUEST),
) -> StaffOrderView:
    order = await order_workflow.request_cancellation(db, ctx, meta, order_id, body.reason)
    return await _view(db, ctx, order)


@router.post("/{order_id}/cancellation-request/{decision}", response_model=StaffOrderView)
async def review_cancellation(
    order_id: uuid.UUID,
    decision: Literal["approve", "reject"],
    db: DbSession,
    meta: MetaDep,
    body: ReviewCancellationRequest | None = None,
    ctx: StaffContext = require(Permission.ORDERS_CANCEL_REVIEW),
) -> StaffOrderView:
    order = await order_workflow.review_cancellation(
        db,
        ctx,
        meta,
        order_id,
        approve=decision == "approve",
        note=(body.note or None) if body else None,
    )
    return await _view(db, ctx, order)
