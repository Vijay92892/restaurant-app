"""Database engine and tenant-bound sessions.

Every transaction that touches tenant data runs with the transaction-local setting
`app.restaurant_id`, which the RLS policies compare against. The tenant is attached to the
session with `bind_tenant()` and re-applied automatically at the start of every transaction
(`after_begin`), so a commit followed by further work never silently loses tenant scope.
Without a bound tenant the policies see NULL and every tenant table appears empty (fail-closed).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.core.config import Settings

_TENANT_KEY = "restaurant_id"
_SET_TENANT_SQL = text("SELECT set_config('app.restaurant_id', :rid, true)")


class TenantBindingError(RuntimeError):
    """Raised when code tries to re-bind a session to a different tenant."""


class TenantSession(Session):
    """Sync session class underlying every AsyncSession we create."""


@event.listens_for(TenantSession, "after_begin")
def _apply_tenant(session: Session, _transaction: object, connection: object) -> None:
    restaurant_id = session.info.get(_TENANT_KEY)
    if restaurant_id is not None:
        connection.execute(_SET_TENANT_SQL, {"rid": str(restaurant_id)})  # type: ignore[attr-defined]


async def bind_tenant(session: AsyncSession, restaurant_id: uuid.UUID) -> None:
    """Scope `session` to one restaurant for the rest of its life."""
    if not isinstance(restaurant_id, uuid.UUID):
        raise TypeError("restaurant_id must be a UUID")
    current = session.info.get(_TENANT_KEY)
    if current is not None:
        if current != restaurant_id:
            raise TenantBindingError("session is already bound to a different restaurant")
        return
    session.info[_TENANT_KEY] = restaurant_id
    if session.in_transaction():
        await session.execute(_SET_TENANT_SQL, {"rid": str(restaurant_id)})


def bound_tenant(session: AsyncSession) -> uuid.UUID | None:
    return session.info.get(_TENANT_KEY)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            # Never let a statement hang a worker indefinitely.
            connect_args={"server_settings": {"statement_timeout": "15000"}},
        )
        self.sessionmaker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            sync_session_class=TenantSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
