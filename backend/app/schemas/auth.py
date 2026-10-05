from __future__ import annotations

import uuid
from typing import Literal

from app.schemas.common import (
    Email,
    NewPassword,
    OpaqueToken,
    Password,
    RequestModel,
    ResponseModel,
    RestaurantCode,
    Username,
)


class LoginRequest(RequestModel):
    restaurant_code: RestaurantCode
    username: Username
    password: Password


class RefreshRequest(RequestModel):
    refresh_token: OpaqueToken


class ChangePasswordRequest(RequestModel):
    current_password: Password
    new_password: NewPassword


class PasswordResetRequest(RequestModel):
    restaurant_code: RestaurantCode
    email: Email


class PasswordResetConfirm(RequestModel):
    token: OpaqueToken
    new_password: NewPassword


class MeResponse(ResponseModel):
    id: uuid.UUID
    restaurant_id: uuid.UUID
    restaurant_name: str
    restaurant_code: str
    username: str
    display_name: str
    role: str
    must_change_password: bool
    permissions: list[str]


class TokenPairResponse(ResponseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str
    user: MeResponse
