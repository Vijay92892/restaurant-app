from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints

from app.schemas.common import RequestModel, ResponseModel


class OpenSessionRequest(RequestModel):
    # Shape is checked against the restaurant's PIN length in the service, so a malformed PIN
    # fails exactly like a wrong one (no format oracle).
    pin: Annotated[str, StringConstraints(min_length=1, max_length=12)]


class TableLandingResponse(ResponseModel):
    restaurant_name: str
    table_label: str
    pin_length: int
    has_session: bool
    # Branding, public like the name: a built-in theme of the ordering page and a tagline.
    theme: str | None = None
    tagline: str | None = None


class CustomerSessionResponse(ResponseModel):
    restaurant_name: str
    table_label: str
    expires_at: datetime
