from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints, model_validator

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
from app.schemas.public import Latitude, Longitude


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


class CustomerAccessRequest(RequestModel):
    """How guests open a table session (owner and managers). The whole policy, every time."""

    location_ordering_enabled: bool
    # The branch's point, confirmed by the owner or a manager (e.g. copied from the map pin).
    latitude: Latitude | None
    longitude: Longitude | None
    radius_m: Annotated[int, Field(ge=25, le=500)]
    # With location access on: may guests whose location check fails still use the PIN?
    pin_fallback_enabled: bool

    @model_validator(mode="after")
    def _check(self) -> CustomerAccessRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("give both latitude and longitude, or neither")
        if self.latitude == 0 and self.longitude == 0:
            raise ValueError("0, 0 is not the restaurant's location")
        if self.location_ordering_enabled and self.latitude is None:
            raise ValueError("set the restaurant's location before turning location access on")
        return self


class CustomerAccessResponse(ResponseModel):
    location_ordering_enabled: bool
    latitude: float | None
    longitude: float | None
    radius_m: int
    pin_fallback_enabled: bool
    # How precise a phone's position report must be (server setting), for the explanation.
    max_accuracy_m: int


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
