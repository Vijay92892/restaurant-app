"""Staff login sessions and their rotating refresh tokens (tenant-scoped)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession, RefreshToken, Restaurant, User


async def create_session(
    db: AsyncSession,
    *,
    restaurant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> AuthSession:
    session = AuthSession(
        id=uuid.uuid4(),
        restaurant_id=restaurant_id,
        user_id=user_id,
        created_at=now,
        last_used_at=now,
        expires_at=expires_at,
        user_agent=user_agent,
        created_ip=ip,
    )
    db.add(session)
    await db.flush()
    return session


async def add_refresh_token(
    db: AsyncSession,
    *,
    restaurant_id: uuid.UUID,
    session_id: uuid.UUID,
    token_hash: bytes,
    now: datetime,
    expires_at: datetime,
    parent_id: uuid.UUID | None = None,
) -> RefreshToken:
    token = RefreshToken(
        id=uuid.uuid4(),
        restaurant_id=restaurant_id,
        session_id=session_id,
        parent_id=parent_id,
        token_hash=token_hash,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(token)
    await db.flush()
    return token


async def get_refresh_token_for_update(
    db: AsyncSession, restaurant_id: uuid.UUID, token_hash: bytes
) -> RefreshToken | None:
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.restaurant_id == restaurant_id, RefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_session_for_update(
    db: AsyncSession, restaurant_id: uuid.UUID, session_id: uuid.UUID
) -> AuthSession | None:
    result = await db.execute(
        select(AuthSession)
        .where(AuthSession.restaurant_id == restaurant_id, AuthSession.id == session_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_live_children_for_update(
    db: AsyncSession, restaurant_id: uuid.UUID, parent_id: uuid.UUID
) -> list[RefreshToken]:
    result = await db.execute(
        select(RefreshToken)
        .where(
            RefreshToken.restaurant_id == restaurant_id,
            RefreshToken.parent_id == parent_id,
            RefreshToken.revoked_at.is_(None),
        )
        .with_for_update()
    )
    return list(result.scalars())


async def load_principal(
    db: AsyncSession, restaurant_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[User, AuthSession, Restaurant] | None:
    result = await db.execute(
        select(User, AuthSession, Restaurant)
        .join(
            AuthSession,
            (AuthSession.user_id == User.id) & (AuthSession.restaurant_id == User.restaurant_id),
        )
        .join(Restaurant, Restaurant.id == User.restaurant_id)
        .where(
            User.restaurant_id == restaurant_id,
            User.id == user_id,
            AuthSession.id == session_id,
        )
    )
    row = result.first()
    return (row[0], row[1], row[2]) if row else None


async def revoke_session(
    db: AsyncSession, restaurant_id: uuid.UUID, session_id: uuid.UUID, reason: str, now: datetime
) -> None:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.restaurant_id == restaurant_id,
            AuthSession.id == session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason=reason)
    )
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.restaurant_id == restaurant_id,
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def revoke_user_sessions(
    db: AsyncSession,
    restaurant_id: uuid.UUID,
    user_id: uuid.UUID,
    reason: str,
    now: datetime,
    *,
    except_session_id: uuid.UUID | None = None,
) -> int:
    conditions = [
        AuthSession.restaurant_id == restaurant_id,
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    ]
    if except_session_id is not None:
        conditions.append(AuthSession.id != except_session_id)
    result = await db.execute(
        update(AuthSession)
        .where(*conditions)
        .values(revoked_at=now, revoke_reason=reason)
        .returning(AuthSession.id)
    )
    session_ids = list(result.scalars())
    if session_ids:
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.restaurant_id == restaurant_id,
                RefreshToken.session_id.in_(session_ids),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    return len(session_ids)
