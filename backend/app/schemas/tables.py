from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel, TableLabel

SortOrder = Annotated[int, Field(ge=0, le=100000)]


class CreateTableRequest(RequestModel):
    label: TableLabel  # type: ignore[valid-type]
    sort_order: SortOrder = 0


class UpdateTableRequest(RequestModel):
    label: TableLabel | None = None  # type: ignore[valid-type]
    is_active: bool | None = None
    sort_order: SortOrder | None = None


class TableResponse(ResponseModel):
    id: uuid.UUID
    label: str
    is_active: bool
    sort_order: int
    qr_url: str | None
    active_sessions: int
    open_orders: int
    ready_orders: int


class QrResponse(ResponseModel):
    qr_url: str


class CloseTableResponse(ResponseModel):
    sessions_ended: int
    orders_completed: int
