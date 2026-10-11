"""Staff account management, with role-escalation guards enforced server-side.

Rules:
  * nobody creates or promotes to `owner` (ownership transfer is a separate, future action);
  * only holders of `staff.manage_admins` (the owner) create, modify or remove admins;
  * the owner account and the caller's own account cannot be changed through these endpoints
    (use change-password / profile for yourself);
  * deactivation, deletion and password resets revoke the target's sessions immediately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.net import RequestMeta
from app.core.permissions import Permission
from app.core.security import passwords, validate_new_password
from app.db.errors import violated_constraint
from app.models import User
from app.models.enums import ActorType, Role, SessionRevokeReason
from app.repositories import auth_sessions
from app.services import audit
from app.services.auth_service import StaffContext

ASSIGNABLE_ROLES = (Role.ADMIN, Role.WAITER, Role.KITCHEN)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def list_staff(db: AsyncSession, restaurant_id: uuid.UUID) -> list[User]:
    result = await db.execute(
        select(User)
        .where(User.restaurant_id == restaurant_id, User.deleted_at.is_(None))
        .order_by(User.role, User.username)
    )
    return list(result.scalars())


async def _get_target(db: AsyncSession, ctx: StaffContext, user_id: uuid.UUID) -> User:
    result = await db.execute(
        select(User).where(
            User.restaurant_id == ctx.restaurant_id,
            User.id == user_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFound()  # includes other restaurants' users: no existence oracle
    return user


def _guard(ctx: StaffContext, target: User | None, new_role: str | None) -> None:
    if new_role is not None and new_role not in ASSIGNABLE_ROLES:
        raise InvalidInput("That role cannot be assigned.", code="invalid_role")
    if target is not None:
        if target.id == ctx.user_id:
            raise Forbidden("You cannot change your own account here.")
        if target.role == Role.OWNER:
            raise Forbidden("The owner account cannot be changed here.")
    touches_admin = Role.ADMIN in {new_role, target.role if target else None}
    if touches_admin and Permission.STAFF_MANAGE_ADMINS not in ctx.permissions:
        raise Forbidden("Only the owner can manage admin accounts.")


def _conflict_from(exc: IntegrityError) -> Conflict:
    name = violated_constraint(exc)
    if name == "uq_users_username":
        return Conflict("That username is already in use.", code="username_taken")
    if name == "uq_users_email":
        return Conflict("That email address is already in use.", code="email_taken")
    return Conflict()


async def create_staff(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    *,
    username: str,
    display_name: str,
    role: str,
    email: str | None,
    temporary_password: str,
) -> User:
    _guard(ctx, None, role)
    validate_new_password(temporary_password, username=username)
    user = User(
        id=uuid.uuid4(),
        restaurant_id=ctx.restaurant_id,
        username=username,
        display_name=display_name,
        role=role,
        email=email,
        password_hash=await passwords.hash(temporary_password),
        must_change_password=True,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise _conflict_from(exc) from exc
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="staff.created",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=user.id,
        details={"username": username, "role": role},
        meta=meta,
    )
    await db.commit()
    return user


async def update_staff(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    user_id: uuid.UUID,
    changes: dict[str, Any],
) -> User:
    target = await _get_target(db, ctx, user_id)
    _guard(ctx, target, changes.get("role"))
    changed = {k: v for k, v in changes.items() if getattr(target, k) != v}
    previous_role = target.role
    for key, value in changed.items():
        setattr(target, key, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise _conflict_from(exc) from exc

    now = utcnow()
    if changed.get("is_active") is False:
        await auth_sessions.revoke_user_sessions(
            db, ctx.restaurant_id, target.id, SessionRevokeReason.USER_DEACTIVATED, now
        )
    actions = []
    if "role" in changed:
        actions.append(("staff.role_changed", {"from_role": previous_role, "to_role": target.role}))
    if "is_active" in changed:
        actions.append(("staff.deactivated" if not target.is_active else "staff.reactivated", {}))
    if changed.keys() - {"role", "is_active"}:
        actions.append(
            ("staff.updated", {"fields": sorted(changed.keys() - {"role", "is_active"})})
        )
    for action, details in actions:
        await audit.record(
            db,
            restaurant_id=ctx.restaurant_id,
            action=action,
            actor_type=ActorType.STAFF,
            actor_user_id=ctx.user_id,
            entity_type="user",
            entity_id=target.id,
            details=details,
            meta=meta,
        )
    await db.commit()
    return target


async def reset_password(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    user_id: uuid.UUID,
    temporary_password: str,
) -> None:
    target = await _get_target(db, ctx, user_id)
    _guard(ctx, target, None)
    await _set_temporary_password(db, ctx, meta, target, temporary_password, by_operator=False)


async def operator_reset_password(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    username: str,
    temporary_password: str,
) -> User:
    """The operator's reset (`python -m app.cli reset-password`): any account of the restaurant,
    the owner's included, for when nobody who could reset it in the app can sign in and the
    reset email cannot help. Same effect as a manager's reset."""
    result = await db.execute(
        select(User).where(
            User.restaurant_id == ctx.restaurant_id,
            User.username == username,
            User.deleted_at.is_(None),
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFound(f"No account {username!r} in this restaurant.")
    await _set_temporary_password(db, ctx, meta, target, temporary_password, by_operator=True)
    return target


async def _set_temporary_password(
    db: AsyncSession,
    ctx: StaffContext,
    meta: RequestMeta,
    target: User,
    temporary_password: str,
    *,
    by_operator: bool,
) -> None:
    """A temporary password, changed at the next sign-in; every session of the account ends."""
    validate_new_password(temporary_password, username=target.username)
    now = utcnow()
    target.password_hash = await passwords.hash(temporary_password)
    target.password_changed_at = now
    target.must_change_password = True
    await auth_sessions.revoke_user_sessions(
        db, ctx.restaurant_id, target.id, SessionRevokeReason.ADMIN_RESET, now
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="staff.password_reset",
        actor_type=ActorType.SYSTEM if by_operator else ActorType.STAFF,
        actor_user_id=None if by_operator else ctx.user_id,
        entity_type="user",
        entity_id=target.id,
        details={"by": "operator"} if by_operator else None,
        meta=meta,
    )
    await db.commit()


async def delete_staff(
    db: AsyncSession, ctx: StaffContext, meta: RequestMeta, user_id: uuid.UUID
) -> None:
    target = await _get_target(db, ctx, user_id)
    _guard(ctx, target, None)
    now = utcnow()
    target.deleted_at = now
    target.is_active = False
    await auth_sessions.revoke_user_sessions(
        db, ctx.restaurant_id, target.id, SessionRevokeReason.USER_DELETED, now
    )
    await audit.record(
        db,
        restaurant_id=ctx.restaurant_id,
        action="staff.deleted",
        actor_type=ActorType.STAFF,
        actor_user_id=ctx.user_id,
        entity_type="user",
        entity_id=target.id,
        details={"username": target.username, "role": target.role},
        meta=meta,
    )
    await db.commit()
