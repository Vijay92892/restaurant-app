from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, col_created_at, col_timestamp, col_updated_at, tenant_id, uuid_pk


class DiningTable(Base):
    __tablename__ = "dining_tables"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    label: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class TableQrToken(Base):
    """Public, unguessable table identifier printed in the QR code. Not a credential."""

    __tablename__ = "table_qr_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "restaurant_id"], ["dining_tables.id", "dining_tables.restaurant_id"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    table_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = col_created_at()
    revoked_at: Mapped[datetime | None] = col_timestamp()


class PinVersion(Base):
    """One generation of a restaurant's table-ordering PIN. Plaintext is never stored."""

    __tablename__ = "pin_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    pin_hmac: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pin_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = col_created_at()
    superseded_at: Mapped[datetime | None] = col_timestamp()


class TableSession(Base):
    """A customer's temporary access to exactly one table of one restaurant."""

    __tablename__ = "table_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "restaurant_id"], ["dining_tables.id", "dining_tables.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["ended_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["restaurant_id", "pin_version"], ["pin_versions.restaurant_id", "pin_versions.version"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    table_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    pin_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = col_created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = col_created_at()
    ended_at: Mapped[datetime | None] = col_timestamp()
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
