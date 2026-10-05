from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKeyConstraint,
    Identity,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, col_created_at, col_timestamp, col_updated_at, tenant_id, uuid_pk


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "restaurant_id"], ["dining_tables.id", "dining_tables.restaurant_id"]
        ),
        # A customer order's table must be its session's table (enforced by the database).
        ForeignKeyConstraint(
            ["table_session_id", "table_id", "restaurant_id"],
            ["table_sessions.id", "table_sessions.table_id", "table_sessions.restaurant_id"],
        ),
        ForeignKeyConstraint(
            ["placed_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["cancel_requested_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["cancelled_by_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    order_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    table_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    table_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    placed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="placed")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = col_timestamp()
    cancel_request_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cancelled_at: Mapped[datetime | None] = col_timestamp()
    cancel_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_at: Mapped[datetime | None] = col_timestamp()
    preparing_at: Mapped[datetime | None] = col_timestamp()
    ready_at: Mapped[datetime | None] = col_timestamp()
    served_at: Mapped[datetime | None] = col_timestamp()
    completed_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class OrderItem(Base):
    """One line of one order. Never merged with other orders' lines."""

    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "restaurant_id"], ["orders.id", "orders.restaurant_id"]),
        ForeignKeyConstraint(
            ["menu_item_id", "restaurant_id"], ["menu_items.id", "menu_items.restaurant_id"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    menu_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = col_created_at()


class OrderItemModifier(Base):
    __tablename__ = "order_item_modifiers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_item_id", "restaurant_id"], ["order_items.id", "order_items.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["modifier_id", "restaurant_id"], ["menu_modifiers.id", "menu_modifiers.restaurant_id"]
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    order_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    modifier_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    modifier_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_price_delta_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class OrderEvent(Base):
    """Append-only order lifecycle log (the spec's `kitchen_events`)."""

    __tablename__ = "order_events"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "restaurant_id"], ["orders.id", "orders.restaurant_id"]),
        ForeignKeyConstraint(
            ["actor_user_id", "restaurant_id"], ["users.id", "users.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["actor_table_session_id", "restaurant_id"],
            ["table_sessions.id", "table_sessions.restaurant_id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_table_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = col_created_at()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
