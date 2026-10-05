"""Security primitives: password hashing, opaque tokens, access JWTs, key derivation."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings
from app.core.errors import InvalidInput, Unauthorized

# --------------------------------------------------------------------------------------------
# Passwords (Argon2id)
# --------------------------------------------------------------------------------------------

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty123",
        "qwertyuiop",
        "11111111",
        "00000000",
        "iloveyou",
        "admin123",
        "welcome1",
        "letmein1",
        "abc12345",
        "restaurant",
        "restaurant1",
        "kitchen1",
        "waiter123",
        "india123",
        "changeme",
        "changeme1",
        "p@ssw0rd",
        "passw0rd",
    }
)


class _PasswordHashing:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(type=Type.ID)
        self._max_concurrency = 4
        self._limiter: anyio.CapacityLimiter | None = None
        self._dummy_hash: str | None = None

    def configure(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._max_concurrency = settings.argon2_max_concurrency
        self._limiter = None
        self._dummy_hash = None

    def _get_limiter(self) -> anyio.CapacityLimiter:
        # Each hash uses `memory_cost` KiB; bounding concurrency bounds memory under load.
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(self._max_concurrency)
        return self._limiter

    async def hash(self, password: str) -> str:
        return await anyio.to_thread.run_sync(
            self._hasher.hash, password, limiter=self._get_limiter()
        )

    async def verify(self, password_hash: str, password: str) -> tuple[bool, bool]:
        """Return (matches, needs_rehash)."""

        def _verify() -> tuple[bool, bool]:
            try:
                self._hasher.verify(password_hash, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False, False
            return True, self._hasher.check_needs_rehash(password_hash)

        return await anyio.to_thread.run_sync(_verify, limiter=self._get_limiter())

    async def burn(self, password: str) -> None:
        """Spend the same time as a real verification (unknown user / inactive account)."""
        if self._dummy_hash is None:
            self._dummy_hash = await self.hash(secrets.token_urlsafe(16))
        await self.verify(self._dummy_hash, password)


passwords = _PasswordHashing()


def validate_new_password(password: str, *, username: str | None = None) -> None:
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        raise InvalidInput(
            f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.",
            code="weak_password",
        )
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS or len(set(password)) < 4:
        raise InvalidInput("This password is too easy to guess.", code="weak_password")
    if username and username.lower() in lowered:
        raise InvalidInput("Password must not contain the username.", code="weak_password")


# --------------------------------------------------------------------------------------------
# Opaque tokens (refresh tokens, reset tokens, table-session cookies)
# --------------------------------------------------------------------------------------------


def new_opaque_token() -> str:
    """256-bit random token, URL-safe (43 chars)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Tokens are high-entropy, so a fast hash is appropriate; only the hash is stored."""
    return hashlib.sha256(token.encode("utf-8")).digest()


# --------------------------------------------------------------------------------------------
# Key derivation
# --------------------------------------------------------------------------------------------


def derive_key(secret: str, purpose: str, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=None, info=f"restaurant/{purpose}".encode()
    ).derive(secret.encode("utf-8"))


# --------------------------------------------------------------------------------------------
# Access tokens (JWT, HS256, short-lived)
# --------------------------------------------------------------------------------------------

_JWT_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub", "rid", "sid", "jti", "typ"]


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    restaurant_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


def _jwt_key(settings: Settings) -> bytes:
    return derive_key(settings.jwt_secret.get_secret_value(), "jwt/v1")


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.access_token_ttl_seconds)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "rid": str(restaurant_id),
        "sid": str(session_id),
        "typ": "access",
        "jti": secrets.token_urlsafe(12),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, _jwt_key(settings), algorithm=_JWT_ALGORITHM), expires_at


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    if not token or len(token) > 2048:
        raise Unauthorized()
    try:
        payload = jwt.decode(
            token,
            _jwt_key(settings),
            algorithms=[_JWT_ALGORITHM],  # pinned: no "none", no algorithm confusion
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=5,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Session expired.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized() from exc
    if payload.get("typ") != "access":
        raise Unauthorized()
    try:
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            restaurant_id=uuid.UUID(payload["rid"]),
            session_id=uuid.UUID(payload["sid"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise Unauthorized() from exc
