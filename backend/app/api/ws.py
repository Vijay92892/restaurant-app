"""Staff WebSocket: live order/menu/PIN events for one restaurant.

Protocol (docs/04-api-contracts.md §8):
  1. the client connects to /api/v1/ws/staff and, within WS_AUTH_TIMEOUT_SECONDS, sends
     {"type": "auth", "access_token": "..."}; the token is never put in the URL (proxy logs);
  2. the server answers {"type": "ready", ...} and from then on pushes event envelopes for the
     caller's restaurant only;
  3. the client may send {"type": "ping"} (answered with "pong") or a fresh {"type": "auth"}
     before its access token expires; anything else is ignored; floods are cut off.

Events are hints plus the current order snapshot; clients still treat REST as the source of
truth and re-fetch after (re)connecting or on a 4408.

Close codes: 4401 unauthenticated/expired/revoked, 4403 forbidden origin or permission,
4408 fell behind (resync required), 4429 too many messages or connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.errors import AppError, RateLimited
from app.core.net import request_meta
from app.core.permissions import Permission
from app.core.rate_limit import Limits
from app.core.security import decode_access_token
from app.core.usage import Metric
from app.services.auth_service import StaffContext, load_staff_context

router = APIRouter()

MAX_MESSAGE_BYTES = 8192
MESSAGES_PER_WINDOW = 30
WINDOW_SECONDS = 10.0


@dataclass
class Principal:
    user_id: uuid.UUID
    restaurant_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


class _Close(Exception):  # noqa: N818 - control flow, not an error
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _check_context(ctx: StaffContext) -> None:
    if ctx.user.must_change_password:
        raise _Close(4401, "password_change_required")
    if Permission.ORDERS_READ not in ctx.permissions:
        raise _Close(4403, "forbidden")


async def _load(
    websocket: WebSocket, restaurant_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    # A short-lived DB session each time: idle sockets must not hold database connections.
    async with websocket.app.state.db.sessionmaker() as db:
        try:
            ctx = await load_staff_context(db, restaurant_id, user_id, session_id)
        except AppError as exc:
            raise _Close(4401, exc.code) from exc
    _check_context(ctx)


async def _authenticate(websocket: WebSocket, token: Any) -> Principal:
    if not isinstance(token, str) or not token:
        raise _Close(4401, "unauthorized")
    try:
        claims = decode_access_token(websocket.app.state.settings, token)
    except AppError as exc:
        raise _Close(4401, exc.code) from exc
    await _load(websocket, claims.restaurant_id, claims.user_id, claims.session_id)
    return Principal(claims.user_id, claims.restaurant_id, claims.session_id, claims.expires_at)


async def _receive_json(websocket: WebSocket) -> Any:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is None:  # a binary frame: the protocol is JSON text only
        raise _Close(1003, "text frames only")
    if len(text.encode()) > MAX_MESSAGE_BYTES:
        raise _Close(1009, "message too big")
    try:
        return json.loads(text)
    except (ValueError, RecursionError):  # not JSON, or nested deeper than the parser allows
        return None


@router.websocket("/ws/staff")
async def staff_socket(websocket: WebSocket) -> None:
    app = websocket.app
    settings = app.state.settings
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        # Native apps send no Origin; a page from an unknown site may not connect (CSWSH).
        await websocket.close(code=4403, reason="origin not allowed")
        return
    await websocket.accept()
    try:
        ip = request_meta(websocket, settings.trusted_proxies).ip
        await app.state.rate_limiter.enforce(Limits.WS_CONNECT_PER_IP, ip)
    except RateLimited:
        await websocket.close(code=4429, reason="too many connections")
        return

    principal: Principal | None = None
    counted = False
    subscription = None
    try:
        try:
            first = await asyncio.wait_for(
                _receive_json(websocket), settings.ws_auth_timeout_seconds
            )
        except TimeoutError as exc:
            raise _Close(4401, "auth timeout") from exc
        if not isinstance(first, dict) or first.get("type") != "auth":
            raise _Close(4401, "unauthorized")
        principal = await _authenticate(websocket, first.get("access_token"))
        me = principal

        connections: dict[uuid.UUID, int] = app.state.ws_connections
        if connections.get(me.user_id, 0) >= settings.ws_max_connections_per_user:
            raise _Close(4429, "too many connections")
        connections[me.user_id] = connections.get(me.user_id, 0) + 1
        counted = True

        subscription = app.state.bus.subscribe(me.restaurant_id)
        await websocket.send_json(
            {
                "type": "ready",
                "restaurant_id": str(me.restaurant_id),
                "server_time": datetime.now(UTC).isoformat(),
            }
        )
        app.state.usage.add(me.restaurant_id, Metric.STAFF_CONNECTED)

        async def send_events() -> None:
            while True:
                event = await subscription.next()
                if event is None:
                    raise _Close(4408, "resync required")
                await websocket.send_text(json.dumps(event))

        async def handle_messages() -> None:
            window_start, count = time.monotonic(), 0
            while True:
                message = await _receive_json(websocket)
                now = time.monotonic()
                if now - window_start > WINDOW_SECONDS:
                    window_start, count = now, 0
                count += 1
                if count > MESSAGES_PER_WINDOW:
                    raise _Close(4429, "too many messages")
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif message.get("type") == "auth":
                    renewed = await _authenticate(websocket, message.get("access_token"))
                    if (renewed.user_id, renewed.restaurant_id) != (me.user_id, me.restaurant_id):
                        raise _Close(4401, "identity changed")
                    me.session_id = renewed.session_id
                    me.expires_at = renewed.expires_at
                    await websocket.send_json({"type": "reauthenticated"})
                # Anything else is ignored: staff change data through the REST API only.

        async def watchdog() -> None:
            while True:
                await asyncio.sleep(settings.ws_recheck_seconds)
                if datetime.now(UTC) >= me.expires_at:
                    raise _Close(4401, "token_expired")
                # Logout, deactivation and role changes reach open sockets too.
                await _load(websocket, me.restaurant_id, me.user_id, me.session_id)

        tasks = [asyncio.create_task(job()) for job in (send_events, handle_messages, watchdog)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(BaseException):
                    await task
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
    except _Close as close:
        if close.code == 4408 and principal is not None:
            app.state.usage.add(principal.restaurant_id, Metric.STAFF_RESYNC)
        with contextlib.suppress(Exception):
            await websocket.close(code=close.code, reason=close.reason)
    except WebSocketDisconnect as gone:
        # 1000/1001: the app closed the connection on purpose (sign-out, screen left, app
        # closed); 1012: this server restarting. Anything else (1006 no close frame, 4000
        # missed heartbeat) is a dropped link.
        if principal is not None and counted and gone.code not in (1000, 1001, 1012):
            app.state.usage.add(principal.restaurant_id, Metric.STAFF_CONNECTION_LOST)
    finally:
        if subscription is not None and principal is not None:
            app.state.bus.unsubscribe(principal.restaurant_id, subscription)
        if counted and principal is not None:
            connections = app.state.ws_connections
            connections[principal.user_id] -= 1
            if connections[principal.user_id] <= 0:
                del connections[principal.user_id]
