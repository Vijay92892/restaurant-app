"""Wrappers for the SECURITY DEFINER resolver functions.

These are the only queries that run before a tenant is bound. Each maps an opaque credential to
routing ids; everything afterwards happens under RLS for the returned tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_restaurant_code(db: AsyncSession, code: str) -> uuid.UUID | None:
    result = await db.execute(text("SELECT app_resolve_restaurant_code(:code)"), {"code": code})
    return result.scalar_one_or_none()


async def resolve_qr_token(db: AsyncSession, token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    result = await db.execute(
        text("SELECT restaurant_id, table_id FROM app_resolve_qr_token(:token)"), {"token": token}
    )
    row = result.first()
    return (row.restaurant_id, row.table_id) if row else None


async def resolve_table_session(db: AsyncSession, token_hash: bytes) -> uuid.UUID | None:
    result = await db.execute(text("SELECT app_resolve_table_session(:h)"), {"h": token_hash})
    return result.scalar_one_or_none()


async def resolve_refresh_token(db: AsyncSession, token_hash: bytes) -> uuid.UUID | None:
    result = await db.execute(text("SELECT app_resolve_refresh_token(:h)"), {"h": token_hash})
    return result.scalar_one_or_none()


async def resolve_password_reset_token(db: AsyncSession, token_hash: bytes) -> uuid.UUID | None:
    result = await db.execute(
        text("SELECT app_resolve_password_reset_token(:h)"), {"h": token_hash}
    )
    return result.scalar_one_or_none()


async def due_pin_rotations(db: AsyncSession, now: datetime) -> list[uuid.UUID]:
    result = await db.execute(text("SELECT app_due_pin_rotations(:now)"), {"now": now})
    return list(result.scalars())


async def restaurant_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Every restaurant's id, for background jobs that then work tenant by tenant."""
    result = await db.execute(text("SELECT app_restaurant_ids()"))
    return list(result.scalars())
