"""Shared schema building blocks. Every request model forbids unknown fields."""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.core.errors import InvalidInput

RESTAURANT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{1,63}$")
OPAQUE_TOKEN_RE = r"^[A-Za-z0-9_-]{20,128}$"


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def patch_changes(body: BaseModel, *, nullable: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Fields the client actually sent. An explicit null is only accepted for nullable fields."""
    changes = body.model_dump(exclude_unset=True)
    bad = sorted(k for k, v in changes.items() if v is None and k not in nullable)
    if bad:
        raise InvalidInput(f"These fields cannot be empty: {', '.join(bad)}.")
    if not changes:
        raise InvalidInput("Nothing to update.")
    return changes


def _normalize_identifier(value: object) -> object:
    if isinstance(value, str) and len(value) <= 300:
        return value.strip().lower()
    return value


def _matches(pattern: re.Pattern[str], what: str):  # type: ignore[no-untyped-def]
    def check(value: str) -> str:
        if not pattern.fullmatch(value):
            raise ValueError(f"invalid {what}")
        return value

    return check


RestaurantCode = Annotated[
    str,
    BeforeValidator(_normalize_identifier),
    StringConstraints(min_length=3, max_length=32),
    AfterValidator(_matches(RESTAURANT_CODE_RE, "restaurant code")),
]
Username = Annotated[
    str,
    BeforeValidator(_normalize_identifier),
    StringConstraints(min_length=3, max_length=32),
    AfterValidator(_matches(USERNAME_RE, "username")),
]
Email = Annotated[
    str,
    BeforeValidator(_normalize_identifier),
    StringConstraints(min_length=3, max_length=254),
    AfterValidator(_matches(EMAIL_RE, "email address")),
]
# Passwords are never trimmed or normalised.
Password = Annotated[str, StringConstraints(min_length=1, max_length=128)]
NewPassword = Annotated[str, StringConstraints(min_length=8, max_length=128)]
OpaqueToken = Annotated[str, StringConstraints(pattern=OPAQUE_TOKEN_RE)]
QR_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
# Request models are strict (no "249" → 249 coercion), but JSON has no UUID type, so UUIDs
# arriving as strings must be parsed: relax strictness for this one field type only.
UUIDStr = Annotated[uuid.UUID, Field(strict=False)]


def _clean_text(value: object) -> object:
    """Trim, collapse internal whitespace runs, and refuse control/format characters
    (NUL, newlines, zero-width and bidi-override characters used for spoofing)."""
    if not isinstance(value, str) or len(value) > 2000:
        return value
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
        raise ValueError("must not contain control or invisible characters")
    return " ".join(value.split())


def plain_text(min_length: int, max_length: int) -> object:
    """Single-line human text (names, labels). Stored and rendered as plain text, never HTML."""
    return Annotated[
        str,
        BeforeValidator(_clean_text),
        StringConstraints(min_length=min_length, max_length=max_length),
    ]


def _clean_multiline(value: object) -> object:
    """Like _clean_text but keeps line breaks (normalised to \\n, at most one blank line)."""
    if not isinstance(value, str) or len(value) > 5000:
        return value
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value if ch != "\n"):
        raise ValueError("must not contain control or invisible characters")
    lines = [" ".join(line.split()) for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def multiline_text(max_length: int) -> object:
    """Multi-line human text (descriptions, notes). Always treated as plain text, never HTML."""
    return Annotated[
        str,
        BeforeValidator(_clean_multiline),
        StringConstraints(min_length=0, max_length=max_length),
    ]


DisplayName = plain_text(1, 80)
RestaurantName = plain_text(1, 120)
TableLabel = plain_text(1, 20)
