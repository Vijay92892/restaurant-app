from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base.

    The authoritative DDL (constraints, RLS policies, grants, resolver functions) lives in the
    Alembic migrations; models describe columns and relationships for the ORM.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def tenant_id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("restaurants.id"), nullable=False)


def col_created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def col_updated_at() -> Mapped[datetime]:
    # Kept current by the `app_touch_updated_at` trigger.
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


def col_timestamp() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=True)
