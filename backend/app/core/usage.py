"""Pilot usage counts: how often each step of the customer journey and of the staff connection
happens, per restaurant and day (spec §48 Phase 11: observe QR scanning, PIN usage, network
failures).

Counts only: no IP addresses, devices, sessions or people. Increments are added up in memory
(cheap, never on a request's critical path) and written by `usage_service.run_flusher` about
once a minute; a crash loses at most that minute. Each API worker writes its own increments, and
the database adds them together.
"""

from __future__ import annotations

import uuid
from enum import StrEnum


class Metric(StrEnum):
    QR_OPENED = "qr_opened"  # a table's page was opened (QR scanned or link reloaded)
    PIN_ACCEPTED = "pin_accepted"  # a diner entered the right PIN: a new table session
    PIN_WRONG = "pin_wrong"
    PIN_LOCKED = "pin_locked"  # refused by the table/restaurant guessing limit
    LOCATION_ACCEPTED = "location_accepted"  # a location check opened a table session
    LOCATION_REFUSED = "location_refused"  # outside, inaccurate, stale or reused report
    STAFF_CONNECTED = "staff_connected"  # a staff device opened its live connection
    STAFF_CONNECTION_LOST = "staff_connection_lost"  # ...and lost it without closing it
    STAFF_RESYNC = "staff_resync"  # a device fell behind and had to reload


class UsageCounters:
    """Pending increments, keyed by (restaurant, metric). Bounded: if writes keep failing, new
    keys are dropped rather than growing without limit."""

    MAX_KEYS = 20_000

    def __init__(self) -> None:
        self._pending: dict[tuple[uuid.UUID, Metric], int] = {}

    def add(self, restaurant_id: uuid.UUID, metric: Metric, n: int = 1) -> None:
        key = (restaurant_id, metric)
        if key not in self._pending and len(self._pending) >= self.MAX_KEYS:
            return
        self._pending[key] = self._pending.get(key, 0) + n

    def drain(self) -> dict[tuple[uuid.UUID, Metric], int]:
        """Everything pending, leaving nothing behind."""
        pending, self._pending = self._pending, {}
        return pending

    def pending(self, restaurant_id: uuid.UUID, metric: Metric) -> int:
        return self._pending.get((restaurant_id, metric), 0)
