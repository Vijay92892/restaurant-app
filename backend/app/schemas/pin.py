from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.common import RequestModel, ResponseModel


class RotatePinRequest(RequestModel):
    invalidate_active_sessions: bool = False


class PinSettingsRequest(RequestModel):
    rotation_minutes: Literal[120, 240, 360] | None  # None = manual rotation only
    pin_length: Literal[4, 6]


class PinResponse(ResponseModel):
    pin: str
    version: int
    pin_length: int
    rotated_at: datetime
    rotation_minutes: int | None
    next_rotation_at: datetime | None


class RotatePinResponse(PinResponse):
    sessions_invalidated: int


class InvalidateSessionsResponse(ResponseModel):
    invalidated: int
