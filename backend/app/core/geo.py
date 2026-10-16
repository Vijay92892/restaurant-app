"""Distance on the Earth and the location check for PIN-less table access.

A phone's position report comes from the guest's browser: it can be faked (developer tools,
emulators, GPS-spoofing apps). Passing this check means "the phone *reports* a fresh, precise
position inside the restaurant's radius", never proof that someone is there. The restaurant
decides whether that is good enough (``location_ordering_enabled``), the PIN stays available
as a fallback, and attempts are rate-limited like PIN guesses (docs/03 §4.5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Mean Earth radius (IUGG). The spherical model is within 0.5 % of the ellipsoid: under a
# metre of error at the distances that matter here (tens to hundreds of metres).
EARTH_RADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two points (degrees), by the haversine formula
    (numerically stable for the short distances a geofence deals with)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class PositionReport:
    """What the guest's browser reported (Geolocation API)."""

    latitude: float
    longitude: float
    accuracy_m: float  # the browser's 68 % confidence radius
    captured_at: datetime  # when the phone took the fix (timezone-aware)


@dataclass(frozen=True)
class Geofence:
    latitude: float
    longitude: float
    radius_m: int


class Verdict(StrEnum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    INACCURATE = "inaccurate"  # the phone isn't sure enough where it is
    STALE = "stale"  # too old, or from the future (a replay or a wrong clock)


def judge(
    report: PositionReport,
    fence: Geofence,
    *,
    now: datetime,
    max_accuracy_m: float,
    max_age: timedelta,
    clock_skew: timedelta,
) -> Verdict:
    """Is this report fresh, precise enough, and inside the fence?

    Near the edge a phone's error decides: half its reported accuracy counts in the guest's
    favour (so someone at the far end of the dining room, reported 30 m off, still passes),
    which with the accuracy cap adds at most ``max_accuracy_m / 2`` to the radius.
    """
    if not (math.isfinite(report.accuracy_m) and 0 <= report.accuracy_m <= max_accuracy_m):
        return Verdict.INACCURATE
    if report.captured_at > now + clock_skew or now - report.captured_at > max_age:
        return Verdict.STALE
    distance = distance_m(report.latitude, report.longitude, fence.latitude, fence.longitude)
    if distance - report.accuracy_m / 2 <= fence.radius_m:
        return Verdict.INSIDE
    return Verdict.OUTSIDE
