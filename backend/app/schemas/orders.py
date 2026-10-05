"""Order schemas. Orders carry no totals, amounts or any other money fields — by design."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.common import RequestModel, ResponseModel, UUIDStr, multiline_text, plain_text

MAX_LINES = 30
MAX_LINE_QUANTITY = 50
MAX_TOTAL_QUANTITY = 100
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"

LineNotes = plain_text(0, 200)
OrderNotes = multiline_text(500)


class OrderLineRequest(RequestModel):
    menu_item_id: UUIDStr
    quantity: Annotated[int, Field(ge=1, le=MAX_LINE_QUANTITY)]
    modifier_ids: Annotated[list[UUIDStr], Field(max_length=20)] = []
    notes: LineNotes | None = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _unique_modifiers(self) -> OrderLineRequest:
        if len(set(self.modifier_ids)) != len(self.modifier_ids):
            raise ValueError("modifier_ids must not contain duplicates")
        return self


class CreateOrderRequest(RequestModel):
    items: Annotated[list[OrderLineRequest], Field(min_length=1, max_length=MAX_LINES)]
    notes: OrderNotes | None = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _total_quantity(self) -> CreateOrderRequest:
        if sum(line.quantity for line in self.items) > MAX_TOTAL_QUANTITY:
            raise ValueError(f"an order can contain at most {MAX_TOTAL_QUANTITY} dishes")
        return self


class CancelOrderRequest(RequestModel):
    reason: plain_text(0, 300) | None = None  # type: ignore[valid-type]


# --- staff -------------------------------------------------------------------------------------


class StaffCreateOrderRequest(CreateOrderRequest):
    """A waiter ordering for a table: no PIN needed, the staff role authorises it."""

    table_id: UUIDStr


class TransitionRequest(RequestModel):
    to_status: Literal["accepted", "preparing", "ready", "served", "completed"]
    expected_version: Annotated[int, Field(ge=1)]


class StaffCancelRequest(RequestModel):
    reason_code: Literal[
        "customer_changed_mind",
        "item_unavailable",
        "duplicate",
        "kitchen_issue",
        "mistake",
        "other",
    ]
    reason: plain_text(0, 300) | None = None  # type: ignore[valid-type]
    expected_version: Annotated[int, Field(ge=1)]


class CancellationRequestBody(RequestModel):
    reason: plain_text(1, 300)  # type: ignore[valid-type]


class ReviewCancellationRequest(RequestModel):
    note: plain_text(0, 300) | None = None  # type: ignore[valid-type]


class OrderLineView(ResponseModel):
    id: uuid.UUID
    menu_item_id: uuid.UUID
    name: str
    quantity: int
    notes: str | None
    modifiers: list[str]


class OrderView(ResponseModel):
    id: uuid.UUID
    order_number: int
    status: str
    version: int
    created_at: datetime
    notes: str | None
    cancel_requested: bool
    items: list[OrderLineView]


class StaffOrderView(OrderView):
    table_id: uuid.UUID
    table_label: str
    source: str
    placed_by: str | None  # staff display name for waiter orders
    updated_at: datetime
    ready_at: datetime | None
    cancel_request_reason: str | None
    cancel_requested_by: str | None
    cancel_reason_code: str | None
    cancel_reason: str | None


class CancelledOrderRef(ResponseModel):
    id: uuid.UUID
    version: int
    cancelled_at: datetime


class KitchenBoardView(ResponseModel):
    server_time: datetime  # lets devices with a wrong clock still compute "delayed" correctly
    kitchen_delay_minutes: int
    truncated: bool
    orders: list[StaffOrderView]
    # Cancelled in the last 2 hours: lets a tablet that was offline tell the cook to stop.
    recently_cancelled: list[CancelledOrderRef]
