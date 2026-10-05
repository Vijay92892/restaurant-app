from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import (
    DisplayName,
    Email,
    NewPassword,
    RequestModel,
    ResponseModel,
    Username,
)

# `owner` is deliberately not assignable through the API.
AssignableRole = Literal["admin", "waiter", "kitchen"]


class CreateStaffRequest(RequestModel):
    username: Username
    display_name: DisplayName  # type: ignore[valid-type]
    role: AssignableRole
    email: Email | None = None
    temporary_password: NewPassword


class UpdateStaffRequest(RequestModel):
    display_name: DisplayName | None = None  # type: ignore[valid-type]
    role: AssignableRole | None = None
    email: Email | None = None
    is_active: bool | None = None


class ResetStaffPasswordRequest(RequestModel):
    temporary_password: NewPassword


class StaffResponse(ResponseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    email: str | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
