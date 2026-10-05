from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import RequestModel, ResponseModel, UUIDStr, multiline_text

Category = Literal["order_mistake", "customer_help", "app_confusing", "network", "slow", "other"]


class CreateProblemReportRequest(RequestModel):
    category: Category
    note: multiline_text(500) | None = None  # type: ignore[valid-type]
    order_id: UUIDStr | None = None  # an order of the reporter's own restaurant


class ProblemReportResponse(ResponseModel):
    id: uuid.UUID
    category: str
    note: str | None
    order_id: uuid.UUID | None
    order_number: int | None
    table_label: str | None
    reported_by: str  # display name
    reported_by_role: str
    created_at: datetime
