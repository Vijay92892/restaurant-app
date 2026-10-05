from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints

from app.schemas.common import (
    DisplayName,
    Email,
    NewPassword,
    RequestModel,
    ResponseModel,
    RestaurantCode,
    RestaurantName,
    Username,
)


def _known_timezone(value: str) -> str:
    if value not in zoneinfo.available_timezones():
        raise ValueError("unknown timezone")
    return value


TimeZone = Annotated[str, StringConstraints(max_length=64), AfterValidator(_known_timezone)]


class RegisterRestaurantRequest(RequestModel):
    restaurant_name: RestaurantName  # type: ignore[valid-type]
    restaurant_code: RestaurantCode
    owner_display_name: DisplayName  # type: ignore[valid-type]
    owner_username: Username
    owner_email: Email | None = None
    password: NewPassword
    invite_code: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


class UpdateRestaurantRequest(RequestModel):
    name: RestaurantName | None = None  # type: ignore[valid-type]
    timezone: TimeZone | None = None
    table_session_ttl_minutes: Annotated[int, Field(ge=30, le=720)] | None = None
    waiter_can_cancel_preparing: bool | None = None
    kitchen_delay_minutes: Annotated[int, Field(ge=5, le=120)] | None = None


class RestaurantResponse(ResponseModel):
    id: uuid.UUID
    code: str
    name: str
    timezone: str
    status: str
    pin_length: int
    pin_rotation_minutes: int | None
    table_session_ttl_minutes: int
    waiter_can_cancel_preparing: bool
    kitchen_delay_minutes: int


class AuditLogResponse(ResponseModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_table_session_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict[str, Any]
