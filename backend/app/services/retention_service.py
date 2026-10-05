"""Data retention: delete records nobody needs any more, one restaurant at a time.

What goes, and when (configurable, see ``Settings``):

* staff sign-in sessions whose lifetime ended ``auth_record_retention_days`` ago; their refresh
  tokens go with them (ON DELETE CASCADE);
* refresh tokens that expired over a day ago. An expired token is refused *before* the reuse
  check, so deleting it changes nothing for token-theft detection; rotated tokens of live
  sessions are kept for exactly that reason;
* password-reset links that expired ``auth_record_retention_days`` ago;
* customer table sessions that expired ``table_session_retention_days`` ago and never placed or
  touched an order (sessions with orders stay with their orders);
* staff problem reports older than ``problem_report_retention_days`` (free text that may mention
  guests);
* audit logs older than ``audit_log_retention_days``, through ``app_purge_audit_logs`` — the
  runtime role cannot delete audit rows itself, and that function refuses anything younger
  than 180 days. Purging audit logs is itself audited.

Orders are kept: they are the restaurant's own history and carry no personal data by design.

Deletes run in that restaurant's tenant-bound transaction (RLS applies) in bounded batches, so a
backlog never holds long locks; the next run continues where this one stopped. Cutoffs use the
database clock. One worker runs at a time (advisory lock), like the PIN scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, fields

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import Database, bind_tenant
from app.models.enums import ActorType
from app.repositories import resolvers
from app.services import audit

logger = logging.getLogger("app.retention")

# Arbitrary constant identifying the "retention leader" advisory lock.
_RETENTION_LOCK_KEY = 7_311_240_027
BATCH_SIZE = 5000
# Per restaurant and run; a larger backlog is finished by the following runs.
MAX_BATCHES = 20

# Table names are constants; values are bound parameters. Each statement deletes at most
# :batch rows of the bound restaurant (the explicit filter and RLS both apply).
_REFRESH_TOKENS = text(
    "DELETE FROM refresh_tokens WHERE id IN ("
    " SELECT id FROM refresh_tokens"
    " WHERE restaurant_id = :rid AND expires_at < now() - interval '1 day'"
    " LIMIT :batch)"
)
_AUTH_SESSIONS = text(
    "DELETE FROM auth_sessions WHERE id IN ("
    " SELECT id FROM auth_sessions"
    " WHERE restaurant_id = :rid AND expires_at < now() - make_interval(days => :days)"
    " LIMIT :batch)"
)
_RESET_TOKENS = text(
    "DELETE FROM password_reset_tokens WHERE id IN ("
    " SELECT id FROM password_reset_tokens"
    " WHERE restaurant_id = :rid AND expires_at < now() - make_interval(days => :days)"
    " LIMIT :batch)"
)
_TABLE_SESSIONS = text(
    "DELETE FROM table_sessions WHERE id IN ("
    " SELECT s.id FROM table_sessions s"
    " WHERE s.restaurant_id = :rid AND s.expires_at < now() - make_interval(days => :days)"
    "   AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.table_session_id = s.id)"
    "   AND NOT EXISTS (SELECT 1 FROM order_events e WHERE e.actor_table_session_id = s.id)"
    " LIMIT :batch)"
)
_PROBLEM_REPORTS = text(
    "DELETE FROM problem_reports WHERE id IN ("
    " SELECT id FROM problem_reports"
    " WHERE restaurant_id = :rid AND created_at < now() - make_interval(days => :days)"
    " LIMIT :batch)"
)
_AUDIT_LOGS = text("SELECT app_purge_audit_logs(now() - make_interval(days => :days), :batch)")


@dataclass
class Purged:
    refresh_tokens: int = 0
    auth_sessions: int = 0
    password_reset_tokens: int = 0
    table_sessions: int = 0
    problem_reports: int = 0
    audit_logs: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, f.name) for f in fields(self))

    def add(self, other: Purged) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


async def _purge_batch(
    db: AsyncSession, settings: Settings, restaurant_id: uuid.UUID, batch: int
) -> Purged:
    rid = {"rid": restaurant_id, "batch": batch}
    auth_days = {**rid, "days": settings.auth_record_retention_days}

    async def run(statement: object, params: dict[str, object]) -> int:
        result = await db.execute(statement, params)  # type: ignore[call-overload]
        return int(result.rowcount)

    purged = Purged(
        refresh_tokens=await run(_REFRESH_TOKENS, rid),
        auth_sessions=await run(_AUTH_SESSIONS, auth_days),
        password_reset_tokens=await run(_RESET_TOKENS, auth_days),
        table_sessions=await run(
            _TABLE_SESSIONS, {**rid, "days": settings.table_session_retention_days}
        ),
        problem_reports=await run(
            _PROBLEM_REPORTS, {**rid, "days": settings.problem_report_retention_days}
        ),
    )
    purged.audit_logs = int(
        (
            await db.execute(
                _AUDIT_LOGS, {"days": settings.audit_log_retention_days, "batch": batch}
            )
        ).scalar_one()
    )
    if purged.audit_logs:
        await audit.record(
            db,
            restaurant_id=restaurant_id,
            action="audit_log.purged",
            actor_type=ActorType.SYSTEM,
            details={
                "deleted": purged.audit_logs,
                "retention_days": settings.audit_log_retention_days,
            },
        )
    return purged


async def purge_restaurant(
    db: Database, settings: Settings, restaurant_id: uuid.UUID, *, batch: int = BATCH_SIZE
) -> Purged:
    """Purge one restaurant, one committed batch at a time."""
    total = Purged()
    for _ in range(MAX_BATCHES):
        async with db.sessionmaker() as session:
            await bind_tenant(session, restaurant_id)
            purged = await _purge_batch(session, settings, restaurant_id, batch)
            await session.commit()
        total.add(purged)
        if max(purged.as_dict().values()) < batch:
            break
    return total


async def purge_expired(db: Database, settings: Settings) -> Purged | None:
    """Purge every restaurant. Returns None when another worker is already doing it."""
    total = Purged()
    async with db.sessionmaker() as leader:
        got_lock = (
            await leader.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _RETENTION_LOCK_KEY}
            )
        ).scalar_one()
        if not got_lock:
            return None
        for restaurant_id in await resolvers.restaurant_ids(leader):
            try:
                total.add(await purge_restaurant(db, settings, restaurant_id))
            except Exception:
                logger.exception("retention failed for restaurant %s", restaurant_id)
    return total


async def run_retention(db: Database, settings: Settings) -> None:
    while True:
        try:
            purged = await purge_expired(db, settings)
            if purged is not None and purged.total:
                logger.info("retention purged %s", purged.as_dict())
        except Exception:
            logger.exception("retention run failed")
        await asyncio.sleep(settings.retention_interval_seconds)
