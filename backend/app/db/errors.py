from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def violated_constraint(exc: IntegrityError) -> str | None:
    """Name of the constraint/index behind an IntegrityError (asyncpg), if available."""
    original = exc.orig
    for candidate in (original, getattr(original, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    return None
