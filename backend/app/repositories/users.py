"""Tenant-scoped user queries.

Every query filters by restaurant_id explicitly *and* runs under RLS.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Restaurant, User


async def get_restaurant(db: AsyncSession, restaurant_id: uuid.UUID) -> Restaurant | None:
    result = await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, restaurant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(
            User.restaurant_id == restaurant_id, User.id == user_id, User.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def get_by_username(db: AsyncSession, restaurant_id: uuid.UUID, username: str) -> User | None:
    result = await db.execute(
        select(User).where(
            User.restaurant_id == restaurant_id,
            User.username == username,
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, restaurant_id: uuid.UUID, email: str) -> User | None:
    result = await db.execute(
        select(User).where(
            User.restaurant_id == restaurant_id, User.email == email, User.deleted_at.is_(None)
        )
    )
    return result.scalar_one_or_none()
