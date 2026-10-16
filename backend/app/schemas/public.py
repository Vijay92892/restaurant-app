from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints

from app.schemas.common import RequestModel, ResponseModel


class OpenSessionRequest(RequestModel):
    # Shape is checked against the restaurant's PIN length in the service, so a malformed PIN
    # fails exactly like a wrong one (no format oracle).
    pin: Annotated[str, StringConstraints(min_length=1, max_length=12)]


# A position report, as the browser's Geolocation API gives it. JSON numbers and an ISO time,
# so parsed leniently (request models are strict otherwise); never NaN or infinite.
Latitude = Annotated[float, Field(strict=False, allow_inf_nan=False, ge=-90, le=90)]
Longitude = Annotated[float, Field(strict=False, allow_inf_nan=False, ge=-180, le=180)]


class LocationSessionRequest(RequestModel):
    latitude: Latitude
    longitude: Longitude
    accuracy_m: Annotated[float, Field(strict=False, allow_inf_nan=False, ge=0, le=100_000)]
    captured_at: Annotated[AwareDatetime, Field(strict=False)]


class TableLandingResponse(ResponseModel):
    restaurant_name: str
    table_label: str
    pin_length: int
    has_session: bool
    # Branding, public like the name: a built-in theme of the ordering page and a tagline.
    theme: str | None = None
    tagline: str | None = None
    # How guests may open a session here (never the PIN, never the restaurant's coordinates).
    pin_enabled: bool = True
    location_enabled: bool = False
    location_radius_m: int | None = None


class CustomerSessionResponse(ResponseModel):
    restaurant_name: str
    table_label: str
    expires_at: datetime
