from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import DbSession, MetaDep, SettingsDep, require
from app.core.config import Settings
from app.core.permissions import Permission
from app.schemas.common import patch_changes
from app.schemas.tables import (
    CloseTableResponse,
    CreateTableRequest,
    QrResponse,
    TableResponse,
    UpdateTableRequest,
)
from app.services import table_service
from app.services.auth_service import StaffContext
from app.services.table_service import TableView

router = APIRouter(prefix="/tables", tags=["tables"])


def _response(settings: Settings, view: TableView) -> TableResponse:
    return TableResponse(
        id=view.table.id,
        label=view.table.label,
        is_active=view.table.is_active,
        sort_order=view.table.sort_order,
        qr_url=table_service.qr_url(settings, view.qr_token) if view.qr_token else None,
        active_sessions=view.active_sessions,
        open_orders=view.open_orders,
        ready_orders=view.ready_orders,
    )


async def _single(
    db: DbSession, settings: Settings, ctx: StaffContext, table_id: uuid.UUID
) -> TableResponse:
    views = await table_service.list_tables(db, ctx.restaurant_id, table_id=table_id)
    return _response(settings, views[0])


@router.get("", response_model=list[TableResponse])
async def list_tables(
    db: DbSession, settings: SettingsDep, ctx: StaffContext = require(Permission.TABLES_READ)
) -> list[TableResponse]:
    views = await table_service.list_tables(db, ctx.restaurant_id)
    return [_response(settings, v) for v in views]


@router.post("", response_model=TableResponse, status_code=status.HTTP_201_CREATED)
async def create_table(
    body: CreateTableRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLES_MANAGE),
) -> TableResponse:
    table = await table_service.create_table(
        db, ctx, settings, meta, label=body.label, sort_order=body.sort_order
    )
    return await _single(db, settings, ctx, table.id)


@router.patch("/{table_id}", response_model=TableResponse)
async def update_table(
    table_id: uuid.UUID,
    body: UpdateTableRequest,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLES_MANAGE),
) -> TableResponse:
    await table_service.update_table(db, ctx, meta, table_id, patch_changes(body))
    return await _single(db, settings, ctx, table_id)


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    table_id: uuid.UUID,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLES_MANAGE),
) -> Response:
    await table_service.delete_table(db, ctx, meta, table_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{table_id}/qr/regenerate", response_model=QrResponse)
async def regenerate_qr(
    table_id: uuid.UUID,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLES_MANAGE),
) -> QrResponse:
    token = await table_service.regenerate_qr(db, ctx, meta, table_id)
    return QrResponse(qr_url=table_service.qr_url(settings, token))


@router.get("/{table_id}/qr.svg", response_class=Response)
async def qr_svg(
    table_id: uuid.UUID,
    db: DbSession,
    settings: SettingsDep,
    ctx: StaffContext = require(Permission.TABLES_READ),
) -> Response:
    table = await table_service.get_table(db, ctx.restaurant_id, table_id)
    token = await table_service.live_qr_token(db, ctx.restaurant_id, table.id)
    svg = table_service.render_qr_svg(table_service.qr_url(settings, token))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="table-{table.id}.svg"'},
    )


@router.post("/{table_id}/close", response_model=CloseTableResponse)
async def close_table(
    table_id: uuid.UUID,
    db: DbSession,
    meta: MetaDep,
    ctx: StaffContext = require(Permission.TABLES_CLOSE),
) -> CloseTableResponse:
    ended, completed = await table_service.close_table(db, ctx, meta, table_id)
    return CloseTableResponse(sessions_ended=ended, orders_completed=completed)
