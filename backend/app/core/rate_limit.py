"""Rate limiting.

Fixed-window counters keyed by a hashed identifier: the window starts at the first hit and the
key expires with it, so a crashed worker can never leave a permanent lock behind.

Failure budgets (wrong PINs, wrong passwords) are *taken before* the attempt is checked and
refunded if it succeeds (see `Budget`). Checking first and counting afterwards would let a burst
of simultaneous guesses all pass the check before any of them is counted.

Production uses Redis (shared across workers). The in-memory backend is bounded and meant for
single-process development and tests only.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import RateLimited


@dataclass(frozen=True)
class Limit:
    name: str
    max: int
    window_seconds: int


class Limits:
    # Staff authentication
    LOGIN_PER_IP = Limit("login_ip", 30, 300)
    LOGIN_FAILURES_PER_ACCOUNT = Limit("login_fail_acct", 5, 900)
    LOGIN_FAILURES_PER_IP = Limit("login_fail_ip", 20, 900)
    REFRESH_PER_IP = Limit("refresh_ip", 120, 300)
    # Live-update sockets: generous (a restaurant's tablets share one IP and reconnect after
    # Wi-Fi drops) but it stops one address holding thousands of unauthenticated sockets.
    WS_CONNECT_PER_IP = Limit("ws_connect_ip", 300, 300)
    CHANGE_PASSWORD_FAILURES_PER_USER = Limit("chpw_fail_user", 5, 900)
    # Staff problem reports (pilot): plenty for a busy shift, bounded against a stuck button.
    PROBLEM_REPORTS_PER_USER = Limit("problem_report_user", 20, 3600)
    RESET_REQUEST_PER_IP = Limit("reset_req_ip", 5, 3600)
    RESET_REQUEST_PER_ACCOUNT = Limit("reset_req_acct", 3, 3600)
    RESET_CONFIRM_PER_IP = Limit("reset_confirm_ip", 10, 900)
    # Restaurant registration
    REGISTRATION_PER_IP = Limit("register_ip", 5, 3600)
    # Customer table access. Table/restaurant failure counters are keyed by PIN *version*:
    # guesses against an old PIN are worthless, and rotating gives staff a one-tap way to lift a
    # lockout an attacker caused on purpose.
    QR_LOOKUP_PER_IP = Limit("qr_lookup_ip", 120, 300)
    PIN_ATTEMPTS_PER_IP = Limit("pin_ip", 30, 900)
    PIN_FAILURES_PER_IP = Limit("pin_fail_ip", 5, 900)
    PIN_FAILURES_PER_TABLE = Limit("pin_fail_table", 10, 900)
    PIN_FAILURES_PER_RESTAURANT = Limit("pin_fail_restaurant", 60, 3600)
    # Location-based access (restaurants turn it on): bounded like PIN entry, with a little
    # more room for honest retries (a guest stepping to a window for a better fix).
    LOCATION_ATTEMPTS_PER_IP = Limit("loc_ip", 30, 900)
    LOCATION_FAILURES_PER_IP = Limit("loc_fail_ip", 10, 900)
    LOCATION_FAILURES_PER_TABLE = Limit("loc_fail_table", 30, 900)
    # One position report opens at most one session: the same report sent again is refused
    # for longer than any report stays fresh (max age + clock skew ≤ 1500 s).
    LOCATION_REPORT_ONCE = Limit("loc_report", 1, 1800)
    # Customer ordering. Created orders and attempts are counted separately so a diner who
    # hits a few "sold out" errors is not locked out, while spam is still bounded.
    ORDERS_CREATED_PER_SESSION = Limit("order_created_session", 10, 600)
    ORDER_ATTEMPTS_PER_SESSION = Limit("order_attempt_session", 30, 600)
    ORDER_ATTEMPTS_PER_IP = Limit("order_attempt_ip", 60, 600)
    ORDER_POLL_PER_SESSION = Limit("order_poll", 120, 300)
    ORDER_CANCEL_PER_SESSION = Limit("order_cancel", 10, 600)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    count: int
    retry_after: int


def _key(limit: Limit, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
    return f"rl:{limit.name}:{digest}"


class RateLimiter(ABC):
    @abstractmethod
    async def _incr(self, key: str, window: int) -> tuple[int, int]:
        """Increment and return (count, seconds_until_reset)."""

    @abstractmethod
    async def _get(self, key: str) -> tuple[int, int]:
        """Return (count, seconds_until_reset) without incrementing."""

    @abstractmethod
    async def _delete(self, key: str) -> None: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    async def _decr(self, key: str) -> None:
        """Undo one increment (never below zero; never creates a key)."""

    async def hit(self, limit: Limit, identifier: str) -> Decision:
        count, ttl = await self._incr(_key(limit, identifier), limit.window_seconds)
        return Decision(allowed=count <= limit.max, count=count, retry_after=ttl)

    async def current(self, limit: Limit, identifier: str) -> Decision:
        """Read a counter without counting (for tests and diagnostics, never for decisions)."""
        count, ttl = await self._get(_key(limit, identifier))
        return Decision(allowed=count < limit.max, count=count, retry_after=ttl)

    async def refund(self, limit: Limit, identifier: str) -> None:
        await self._decr(_key(limit, identifier))

    async def reset(self, limit: Limit, identifier: str) -> None:
        await self._delete(_key(limit, identifier))

    async def enforce(self, limit: Limit, identifier: str) -> Decision:
        """Count this request atomically; reject (429) if that takes it over the limit."""
        decision = await self.hit(limit, identifier)
        if not decision.allowed:
            raise RateLimited(decision.retry_after)
        return decision


class Budget:
    """Counts one attempt against failure budgets *before* it is checked.

    `take` reserves a failure atomically (429 if the budget is spent, giving back whatever this
    attempt already took); `refund` gives everything back once the attempt turns out fine. An
    attempt that fails — or crashes — simply keeps its reservations.
    """

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter
        self._taken: list[tuple[Limit, str]] = []

    async def take(self, limit: Limit, identifier: str) -> Decision:
        try:
            decision = await self._limiter.enforce(limit, identifier)
        except RateLimited:
            await self.refund()
            raise
        self._taken.append((limit, identifier))
        return decision

    async def refund(self) -> None:
        taken, self._taken = self._taken, []
        for limit, identifier in taken:
            await self._limiter.refund(limit, identifier)


class MemoryRateLimiter(RateLimiter):
    def __init__(self, max_keys: int = 100_000) -> None:
        self._data: dict[str, tuple[int, float]] = {}
        self._max_keys = max_keys

    def _now(self) -> float:
        return time.monotonic()

    def _prune(self) -> None:
        now = self._now()
        for key in [k for k, (_, exp) in self._data.items() if exp <= now]:
            del self._data[key]
        if len(self._data) >= self._max_keys:
            # Still full of live keys: drop those closest to expiry (bounded memory under attack).
            for key, _ in sorted(self._data.items(), key=lambda kv: kv[1][1])[
                : self._max_keys // 10
            ]:
                del self._data[key]

    async def _incr(self, key: str, window: int) -> tuple[int, int]:
        now = self._now()
        count, expires = self._data.get(key, (0, 0.0))
        if expires <= now:
            if len(self._data) >= self._max_keys:
                self._prune()
            count, expires = 0, now + window
        count += 1
        self._data[key] = (count, expires)
        return count, max(1, int(expires - now + 0.999))

    async def _get(self, key: str) -> tuple[int, int]:
        now = self._now()
        count, expires = self._data.get(key, (0, 0.0))
        if expires <= now:
            return 0, 0
        return count, max(1, int(expires - now + 0.999))

    async def _decr(self, key: str) -> None:
        count, expires = self._data.get(key, (0, 0.0))
        if expires > self._now() and count > 0:
            self._data[key] = (count - 1, expires)  # same window

    async def _delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._redis = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    async def _incr(self, key: str, window: int) -> tuple[int, int]:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window, nx=True)
            pipe.incr(key)
            pipe.ttl(key)
            _, count, ttl = await pipe.execute()
        return int(count), max(1, int(ttl))

    async def _get(self, key: str) -> tuple[int, int]:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            value, ttl = await pipe.execute()
        return (int(value) if value is not None else 0), max(0, int(ttl))

    async def _decr(self, key: str) -> None:
        # If the window expired meanwhile, DECR creates the key at -1 without a TTL: remove it.
        if await self._redis.decr(key) < 0:
            await self._redis.delete(key)

    async def _delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def close(self) -> None:
        await self._redis.aclose()


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for the redis rate-limit backend")
        return RedisRateLimiter(settings.redis_url)
    return MemoryRateLimiter()
