"""Transactional outbox for live events.

Services *stage* events inside their transaction. Only when that transaction commits are the
events released, and they are published after the request's work is done — so a rolled-back
change never reaches a kitchen screen, and a committed one always gets a chance to.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.session import TenantSession
from app.realtime.bus import EventBus

logger = logging.getLogger("app.realtime")

_STAGED = "realtime_staged"
_RELEASED = "realtime_released"


def stage(
    db: AsyncSession,
    restaurant_id: uuid.UUID,
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    order_id: uuid.UUID | None = None,
    order_version: int | None = None,
) -> None:
    envelope = {
        "event_id": uuid.uuid4().hex,
        "type": event_type,
        "restaurant_id": str(restaurant_id),
        "order_id": str(order_id) if order_id else None,
        "order_version": order_version,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }
    if not db.in_transaction():
        # Tie the event to a transaction so that it is released by that transaction's commit
        # or dropped by its rollback (rollback() is a no-op when nothing has begun). This does
        # no I/O: a connection is only taken when the first statement runs.
        db.sync_session.begin()
    db.info.setdefault(_STAGED, []).append((restaurant_id, envelope))


@event.listens_for(TenantSession, "after_commit")
def _release(session: Session) -> None:
    staged = session.info.pop(_STAGED, None)
    if staged:
        session.info.setdefault(_RELEASED, []).extend(staged)


@event.listens_for(TenantSession, "after_soft_rollback")
def _discard(session: Session, previous_transaction: object) -> None:
    # "soft" fires on every rollback() call, even when no SQL had run yet ("after_rollback"
    # only fires for a real DBAPI rollback, which would let a staged event leak into the next
    # commit). Savepoint rollbacks leave the outer transaction's events alone.
    if getattr(previous_transaction, "parent", None) is None:
        session.info.pop(_STAGED, None)


async def publish_released(db: AsyncSession, bus: EventBus) -> None:
    """Publish everything committed in this session. Failures are logged, never raised."""
    released = db.info.pop(_RELEASED, None)
    for restaurant_id, envelope in released or ():
        try:
            await bus.publish(restaurant_id, envelope)
        except Exception:
            logger.exception("publishing %s failed", envelope.get("type"))
