"""Tenant-scoped event bus for live updates to staff devices.

Every event is published to exactly one restaurant's channel; a WebSocket only ever subscribes
to its own restaurant's channel (there is no global stream). With several API workers the Redis
bus fans events out across processes; the in-memory bus serves a single process (dev/tests).

Delivery is best-effort by design: clients treat events as hints and resynchronise from
`GET /kitchen/board` after reconnecting, so a missed event is never a lost order.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from app.core.config import Settings

logger = logging.getLogger("app.realtime")

Event = dict[str, Any]


class Subscription:
    """One WebSocket's queue. If the client cannot keep up, the queue overflows and the socket
    is told to resync instead of silently missing updates."""

    def __init__(self, maxsize: int = 256) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self.overflowed = False
        self._wakeup = asyncio.Event()

    def deliver(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.force_resync()

    def force_resync(self) -> None:
        self.overflowed = True
        self._wakeup.set()

    async def next(self) -> Event | None:
        """Next event, or None when the subscriber fell behind and must resync."""
        if self.overflowed:
            return None
        getter = asyncio.ensure_future(self._queue.get())
        waker = asyncio.ensure_future(self._wakeup.wait())
        try:
            done, _ = await asyncio.wait({getter, waker}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                return getter.result()
            return None
        finally:
            for task in (getter, waker):
                if not task.done():
                    task.cancel()


class EventBus(ABC):
    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = defaultdict(set)

    def subscribe(self, restaurant_id: uuid.UUID) -> Subscription:
        subscription = Subscription()
        self._subscribers[str(restaurant_id)].add(subscription)
        return subscription

    def unsubscribe(self, restaurant_id: uuid.UUID, subscription: Subscription) -> None:
        key = str(restaurant_id)
        self._subscribers[key].discard(subscription)
        if not self._subscribers[key]:
            del self._subscribers[key]

    def _dispatch(self, restaurant_id: str, event: Event) -> None:
        for subscription in list(self._subscribers.get(restaurant_id, ())):
            subscription.deliver(event)

    def _resync_all(self) -> None:
        """Events may have been lost (e.g. a broker reconnect): every client must resync."""
        for subscriptions in list(self._subscribers.values()):
            for subscription in list(subscriptions):
                subscription.force_resync()

    @abstractmethod
    async def publish(self, restaurant_id: uuid.UUID, event: Event) -> None: ...

    async def start(self) -> None:  # noqa: B027 - optional hook
        pass

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


class LocalEventBus(EventBus):
    """Single-process bus."""

    async def publish(self, restaurant_id: uuid.UUID, event: Event) -> None:
        self._dispatch(str(restaurant_id), event)


def channel_for(restaurant_id: uuid.UUID | str) -> str:
    return f"tenant:{restaurant_id}:staff"


class RedisEventBus(EventBus):
    """Cross-process bus: publish to `tenant:<id>:staff`, one listener per process fans out."""

    def __init__(self, redis: Any) -> None:
        super().__init__()
        self._redis = redis
        self._listener: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    @classmethod
    def from_url(cls, url: str) -> RedisEventBus:
        from redis.asyncio import Redis

        return cls(Redis.from_url(url, socket_connect_timeout=2, health_check_interval=30))

    async def start(self) -> None:
        self._listener = asyncio.create_task(self._listen())
        await asyncio.wait_for(self._ready.wait(), timeout=5)

    async def _listen(self) -> None:
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(channel_for("*"))
                if self._ready.is_set():
                    self._resync_all()  # reconnected: anything published meanwhile is gone
                self._ready.set()
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = message["channel"]
                    channel = channel.decode() if isinstance(channel, bytes) else channel
                    restaurant_id = channel.split(":")[1]
                    try:
                        event = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict) and event.get("restaurant_id") == restaurant_id:
                        self._dispatch(restaurant_id, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("redis event listener failed; reconnecting")
                await asyncio.sleep(1)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def publish(self, restaurant_id: uuid.UUID, event: Event) -> None:
        await self._redis.publish(channel_for(restaurant_id), json.dumps(event))

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        await self._redis.aclose()


def build_event_bus(settings: Settings) -> EventBus:
    if settings.event_bus_backend == "redis":
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for the redis event bus")
        return RedisEventBus.from_url(settings.redis_url)
    return LocalEventBus()
