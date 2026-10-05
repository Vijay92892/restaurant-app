"""Dining tables and their QR codes.

Tables are soft-deleted so historical orders keep their table. A table has at most one live QR
token; regenerating revokes the old one (old printed codes stop working immediately).
"""

from __future__ import annotations

import io
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import segno
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import Conflict, NotFound
from app.core.net import RequestMeta
from app.db.errors import violated_constraint
from app.models import DiningTable, Order, TableQrToken
from app.models.enums import ActorType, OrderStatus, TableSessionEndReason
from app.realtime import outbox
from app.services import audit, order_workflow, table_session_service
from app.services.auth_service import StaffContext

_TERMINAL = (OrderStatus.COMPLETED.value, OrderStatus.CANCELLED.value)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_qr_token() -> str:
    return secrets.token_urlsafe(16)  # 128 bits, 22 URL-safe characters


def qr_url(settings: Settings, token: str) -> str:
    return f"{settings.public_order_base_url}/t/{token}"


def render_qr_svg(url: str, *, scalable: bool = False) -> bytes:
    """The QR code as SVG. `scalable` drops the fixed width/height in favour of a viewBox, so a
    page can size it freely (at a fixed size, a smaller box crops the code instead of shrinking
    it, and a cropped code cannot be scanned)."""
    buffer = io.BytesIO()
    segno.make_qr(url, error="m").save(
        buffer, kind="svg", scale=10, border=4, xmldecl=False, omitsize=scalable
    )
    return buffer.getvalue()


@dataclass(frozen=True)
class TableView:
    table: DiningTable
    qr_token: str | None
    active_sessions: int
    open_orders: int
    ready_orders: int


def _label_conflict() -> Conflict:
    return Conflict("A table with this name already exists.", code="table_label_taken")


async def get_table(
    db: AsyncSession, restaurant_id: uuid.UUID, table_id: uuid.UUID, *, lock: bool = False
) -> DiningTable:
    query = select(DiningTable).where(
        DiningTable.restaurant_id == restaurant_id,
        DiningTable.id == table_id,
        DiningTable.deleted_at.is_(None),
    )
    result = await db.execute(query.with_for_update() if lock else query)
    table = result.scalar_one_or_none()
    if table is None:
        raise NotFound()
    return table


async def _live_qr(
    db: AsyncSession, restaurant_id: uuid.UUID, table_id: uuid.UUID
) -> TableQrToken | None:
    result = await db.execute(
        select(TableQrToken).where(
            TableQrToken.restaurant_id == restaurant_id,
            TableQrToken.table_id == table_id,
            TableQrToken.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def live_qr_token(db: AsyncSession, restaurant_id: uuid.UUID, table_id: uuid.UUID) -> str:
    qr = await _live_qr(db, restaurant_id, table_id)
    if qr is None:
        raise NotFound()
    return qr.token


async def list_tables(
    db: AsyncSession, restaurant_id: uuid.UUID, *, table_id: uuid.UUID | None = None
) -> list[TableView]:
    query = (
        select(DiningTable)
        .where(DiningTable.restaurant_id == restaurant_id, DiningTable.deleted_at.is_(None))
        .order_by(DiningTable.sort_order, func.lower(DiningTable.label))
    )
    if table_id is not None:
        query = query.where(DiningTable.id == table_id)
    tables = (await db.execute(query)).scalars().all()
    tokens = dict(
        (
            await db.execute(
                select(TableQrToken.table_id, TableQrToken.token).where(
                    TableQrToken.restaurant_id == restaurant_id, TableQrToken.revoked_at.is_(None)
                )
            )
        ).all()
    )
    sessions = await table_session_service.active_session_counts(db, restaurant_id)
    orders = dict(
        (
            await db.execute(
                select(Order.table_id, func.count())
                .where(Order.restaurant_id == restaurant_id, Order.status.notin_(_TERMINAL))
                .group_by(Order.table_id)
            )
        ).all()
    )
    ready = dict(
        (
            await db.execute(
                select(Order.table_id, func.count())
                .where(
                    Order.restaurant_id == restaurant_id, Order.status == OrderStatus.READY.value
                )
                .group_by(Order.table_id)
            )
        ).all()
    )
    return [
        TableView(
            table=t,
            qr_token=tokens.get(t.id),
            active_sessions=sessions.get(t.id, 0),
            open_orders=orders.get(t.id, 0),
            ready_orders=ready.get(t.id, 0),
        )
        for t in tables
    ]


async def create_table(
    db: AsyncSession,
    ctx: StaffContext,
    settings: Settings,
    meta: RequestMeta,
    *,
    label: str,
    sort_order: int,
) -> DiningTable:
    count = (
        await db.execute(
            select(func.count())
            .select_from(DiningTable)
            .where(DiningTable.restaurant_id == ctx.restaurant_id, DiningTable.deleted_at.is_(None))
        )
    ).scalar_one()
    if count >= settings.max_tables_per_restaurant:
        raise Conflict("This restaurant has reached its table limit.", code="table_limit_reached")

    table = DiningTable(
        id=uuid.uuid4(), restaurant_id=ctx.restaurant_id, label=label, sort_order=sort_order
    )
    db.add(table)
    try:
        await db.flush()
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_dining_tables_label":
            raise _label_conflict() from exc
        raise
    db.add(
        TableQrToken(
            id=uuid.uuid4(),
            restaurant_id=ctx.restaurant_id,
            table_id=table.id,
            token=new_qr_token(),
        )
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="table.created",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="table",
        entity_id=table.id,
        details={"label": label},
        meta=meta,
    )
    await db.commit()
    return table


async def update_table(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    table_id: uuid.UUID,
    changes: dict[str, Any],
) -> DiningTable:
    table = await get_table(db, ctx.restaurant_id, table_id)
    changed = {k: v for k, v in changes.items() if getattr(table, k) != v}
    for key, value in changed.items():
        setattr(table, key, value)
    ended = 0
    if changed.get("is_active") is False:
        # A disabled table must stop accepting customer activity right away.
        ended = await table_session_service.end_active_sessions(
            db,
            ctx.restaurant_id,
            reason=TableSessionEndReason.TABLE_DISABLED,
            actor_user_id=ctx.user_id,
            table_id=table.id,
        )
    try:
        await db.flush()
    except IntegrityError as exc:
        if violated_constraint(exc) == "uq_dining_tables_label":
            raise _label_conflict() from exc
        raise
    if changed:
        await audit.record(
            db,
            restaurant_id=ctx.restaurant_id,
            action="table.updated",
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="table",
            entity_id=table.id,
            details={**{f"new_{k}": v for k, v in changed.items()}, "sessions_ended": ended},
            meta=meta,
        )
    await db.commit()
    return table


async def delete_table(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, table_id: uuid.UUID
) -> None:
    table = await get_table(db, ctx.restaurant_id, table_id)
    now = utcnow()
    table.deleted_at = now
    table.is_active = False
    await db.execute(
        update(TableQrToken)
        .where(
            TableQrToken.restaurant_id == ctx.restaurant_id,
            TableQrToken.table_id == table.id,
            TableQrToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    ended = await table_session_service.end_active_sessions(
        db,
        ctx.restaurant_id,
        reason=TableSessionEndReason.TABLE_DISABLED,
        actor_user_id=ctx.user_id,
        table_id=table.id,
        now=now,
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="table.deleted",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="table",
        entity_id=table.id,
        details={"label": table.label, "sessions_ended": ended},
        meta=meta,
    )
    await db.commit()


async def regenerate_qr(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, table_id: uuid.UUID
) -> str:
    # Locking the table serialises simultaneous regenerations (exactly one live code survives).
    table = await get_table(db, ctx.restaurant_id, table_id, lock=True)
    old = await _live_qr(db, ctx.restaurant_id, table.id)
    if old is not None:
        old.revoked_at = utcnow()
        await db.flush()
    token = new_qr_token()
    db.add(
        TableQrToken(
            id=uuid.uuid4(), restaurant_id=ctx.restaurant_id, table_id=table.id, token=token
        )
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="table.qr_regenerated",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="table",
        entity_id=table.id,
        meta=meta,
    )
    await db.commit()
    return token


async def close_table(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, table_id: uuid.UUID
) -> tuple[int, int]:
    """The party has left: end every customer session at this table so the next party needs
    the current PIN, and mark its served orders completed. Returns (sessions, orders)."""
    table = await get_table(db, ctx.restaurant_id, table_id)
    ended = await table_session_service.end_active_sessions(
        db,
        ctx.restaurant_id,
        reason=TableSessionEndReason.TABLE_CLOSED,
        actor_user_id=ctx.user_id,
        table_id=table.id,
    )
    completed = await order_workflow.complete_served_orders(db, ctx, meta, table.id)
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="table.closed",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="table",
        entity_id=table.id,
        details={"sessions_ended": ended, "orders_completed": completed},
        meta=meta,
    )
    outbox.stage(db, ctx.restaurant_id, "table.closed", payload={"table_id": str(table.id)})
    await db.commit()
    return ended, completed
