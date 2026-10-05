"""Audit logging. Runs inside the caller's transaction so the record commits with the action."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.net import RequestMeta
from app.models import AuditLog
from app.models.enums import ActorType

# Keys that could carry secrets are refused outright: a programming error, caught by tests.
_FORBIDDEN_KEY = re.compile(
    r"pass|secret|token|hash|cookie|authorization|otp|(^|_)pin($|_code)", re.I
)
_MAX_STRING = 200


def _check_details(details: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if _FORBIDDEN_KEY.search(key):
            raise ValueError(f"audit detail key {key!r} may contain a secret")
        if isinstance(value, str):
            clean[key] = value[:_MAX_STRING]
        elif value is None or isinstance(value, bool | int):
            clean[key] = value
        elif isinstance(value, list | tuple) and all(isinstance(v, str | int) for v in value):
            clean[key] = [v[:_MAX_STRING] if isinstance(v, str) else v for v in value][:50]
        else:
            raise ValueError(f"audit detail {key!r} must be a primitive or list of primitives")
    return clean


async def record(
    db: AsyncSession,
    *,
    restaurant_id: uuid.UUID,
    action: str,
    actor_type: ActorType,
    actor_user_id: uuid.UUID | None = None,
    actor_table_session_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    clean_details = _check_details(details or {})
    db.add(
        AuditLog(
            restaurant_id=restaurant_id,
            actor_type=actor_type.value,
            actor_user_id=actor_user_id,
            actor_table_session_id=actor_table_session_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=clean_details,
            ip=meta.ip if meta and meta.ip != "0.0.0.0" else None,  # noqa: S104
            request_id=meta.request_id if meta else None,
        )
    )
