from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKeyConstraint, Integer, SmallInteger, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, col_created_at, col_timestamp, col_updated_at, tenant_id, uuid_pk


class MenuCategory(Base):
    __tablename__ = "menu_categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class MenuItem(Base):
    __tablename__ = "menu_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["category_id", "restaurant_id"],
            ["menu_categories.id", "menu_categories.restaurant_id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    dietary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Informational menu text only. Never summed, totalled, billed or reported.
    display_price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class MenuModifierGroup(Base):
    __tablename__ = "menu_modifier_groups"

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    min_select: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_select: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class MenuModifier(Base):
    __tablename__ = "menu_modifiers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "restaurant_id"],
            ["menu_modifier_groups.id", "menu_modifier_groups.restaurant_id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_price_delta_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = col_timestamp()
    created_at: Mapped[datetime] = col_created_at()
    updated_at: Mapped[datetime] = col_updated_at()


class MenuItemModifierGroup(Base):
    __tablename__ = "menu_item_modifier_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["menu_item_id", "restaurant_id"], ["menu_items.id", "menu_items.restaurant_id"]
        ),
        ForeignKeyConstraint(
            ["group_id", "restaurant_id"],
            ["menu_modifier_groups.id", "menu_modifier_groups.restaurant_id"],
        ),
    )

    menu_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    restaurant_id: Mapped[uuid.UUID] = tenant_id()
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
