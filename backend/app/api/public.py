"""Customer-facing endpoints. No accounts: a table session cookie is the only credential, and
restaurant/table context always comes from the session row, never from the request."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status
from pydantic import TypeAdapter

from app.api.deps import (
    CryptoDep,
    CurrentCustomer,
    DbSession,
    IdempotencyKey,
    LimiterDep,
    MetaDep,
    PublicWrite,
    SettingsDep,
)
from app.core.errors import NotFound
from app.core.rate_limit import Budget, Limits
from app.core.usage import Metric
from app.models.enums import OrderSource
from app.schemas.common import QR_TOKEN_PATTERN
from app.schemas.menu import PublicMenuResponse
from app.schemas.orders import (
    CancelOrderRequest,
    CreateOrderRequest,
    OrderView,
)
from app.schemas.public import CustomerSessionResponse, OpenSessionRequest, TableLandingResponse
from app.services import menu_service, order_service, table_session_service
from app.services.order_service import OrderActor
from app.services.table_session_service import SESSION_COOKIE

router = APIRouter(prefix="/public", tags=["public"])

QrToken = Annotated[str, Path(max_length=64)]
_order_list = TypeAdapter(list[OrderView])


def _json_with_etag(request: Request, body: bytes) -> Response:
    """Conditional JSON: phones on weak networks re-download only when content changed."""
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _checked(token: str) -> str:
    if not re.fullmatch(QR_TOKEN_PATTERN, token):
        raise NotFound("This QR code is not valid.")
    return token


@router.get("/tables/{qr_token}", response_model=TableLandingResponse)
async def table_landing(
    qr_token: QrToken,
    request: Request,
    db: DbSession,
    limiter: LimiterDep,
    meta: MetaDep,
) -> TableLandingResponse:
    await limiter.enforce(Limits.QR_LOOKUP_PER_IP, meta.ip)
    ctx = await table_session_service.resolve_qr(db, _checked(qr_token))
    request.app.state.usage.add(ctx.restaurant.id, Metric.QR_OPENED)
    has_session = await table_session_service.session_matches_table(
        db, request.cookies.get(SESSION_COOKIE), ctx.restaurant.id, ctx.table.id
    )
    return TableLandingResponse(
        restaurant_name=ctx.restaurant.name,
        table_label=ctx.table.label,
        pin_length=ctx.restaurant.pin_length,
        has_session=has_session,
        theme=ctx.restaurant.theme,
        tagline=ctx.restaurant.tagline,
    )


@router.post(
    "/tables/{qr_token}/session",
    response_model=CustomerSessionResponse,
    dependencies=[PublicWrite],
)
async def open_session(
    qr_token: QrToken,
    body: OpenSessionRequest,
    request: Request,
    response: Response,
    db: DbSession,
    crypto: CryptoDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> CustomerSessionResponse:
    token, customer = await table_session_service.open_session(
        db, crypto, limiter, meta, qr_token, body.pin, usage=request.app.state.usage
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=customer.restaurant.table_session_ttl_minutes * 60,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return CustomerSessionResponse(
        restaurant_name=customer.restaurant.name,
        table_label=customer.table.label,
        expires_at=customer.session.expires_at,
    )


@router.get("/menu", response_model=PublicMenuResponse)
async def public_menu(
    customer: CurrentCustomer, request: Request, db: DbSession, settings: SettingsDep
) -> Response:
    """The menu of *this session's* restaurant — never selectable by the client. Served with an
    ETag so phones on weak networks re-download it only when it actually changed."""
    snapshot = await menu_service.load_menu(db, customer.restaurant_id)
    payload = PublicMenuResponse.model_validate(
        {
            "restaurant_name": customer.restaurant.name,
            "table_label": customer.table.label,
            **menu_service.public_view(settings, snapshot),
        }
    )
    return _json_with_etag(request, payload.model_dump_json().encode())


@router.post(
    "/orders",
    response_model=OrderView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[PublicWrite],
    responses={200: {"description": "Retry of an earlier request: the original order"}},
)
async def place_order(
    body: CreateOrderRequest,
    idempotency_key: IdempotencyKey,
    customer: CurrentCustomer,
    response: Response,
    db: DbSession,
    limiter: LimiterDep,
    meta: MetaDep,
) -> OrderView:
    # Context comes only from the session row — the body cannot name a restaurant or table.
    restaurant_id, table_id, session_id = (
        customer.restaurant_id,
        customer.table_id,
        customer.session.id,
    )
    await limiter.enforce(Limits.ORDER_ATTEMPTS_PER_IP, meta.ip)
    await limiter.enforce(Limits.ORDER_ATTEMPTS_PER_SESSION, str(session_id))
    # Reserve the order allowance first (simultaneous requests cannot all slip under the cap);
    # give it back if no new order results (rejected, or a retry of an existing order).
    allowance = Budget(limiter)
    await allowance.take(Limits.ORDERS_CREATED_PER_SESSION, str(session_id))
    try:
        order, created = await order_service.create_order(
            db,
            restaurant_id=restaurant_id,
            table_id=table_id,
            actor=OrderActor(OrderSource.CUSTOMER, table_session_id=session_id),
            request=body,
            idempotency_key=idempotency_key,
        )
    except Exception:
        await allowance.refund()
        raise
    if not created:
        await allowance.refund()
        response.status_code = status.HTTP_200_OK
    views = await order_service.order_views(db, restaurant_id, [order])
    return OrderView.model_validate(views[0])


@router.get("/orders", response_model=list[OrderView])
async def my_orders(
    customer: CurrentCustomer, request: Request, db: DbSession, limiter: LimiterDep
) -> Response:
    """Orders placed from *this* session only (newest first)."""
    await limiter.enforce(Limits.ORDER_POLL_PER_SESSION, str(customer.session.id))
    orders = await order_service.session_orders(db, customer.restaurant_id, customer.session.id)
    views = await order_service.order_views(db, customer.restaurant_id, orders)
    return _json_with_etag(request, _order_list.dump_json(_order_list.validate_python(views)))


@router.post("/orders/{order_id}/cancel", response_model=OrderView, dependencies=[PublicWrite])
async def cancel_order(
    order_id: uuid.UUID,
    customer: CurrentCustomer,
    db: DbSession,
    limiter: LimiterDep,
    meta: MetaDep,
    body: CancelOrderRequest | None = None,
) -> OrderView:
    restaurant_id, session_id = customer.restaurant_id, customer.session.id
    await limiter.enforce(Limits.ORDER_CANCEL_PER_SESSION, str(session_id))
    order = await order_service.customer_cancel(
        db,
        restaurant_id=restaurant_id,
        table_session_id=session_id,
        order_id=order_id,
        reason=body.reason if body else None,
        meta=meta,
    )
    views = await order_service.order_views(db, restaurant_id, [order])
    return OrderView.model_validate(views[0])


@router.get("/session", response_model=CustomerSessionResponse)
async def current_session(customer: CurrentCustomer) -> CustomerSessionResponse:
    return CustomerSessionResponse(
        restaurant_name=customer.restaurant.name,
        table_label=customer.table.label,
        expires_at=customer.session.expires_at,
    )


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT, dependencies=[PublicWrite])
async def leave_table(customer: CurrentCustomer, db: DbSession) -> Response:
    await table_session_service.leave(db, customer)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response
