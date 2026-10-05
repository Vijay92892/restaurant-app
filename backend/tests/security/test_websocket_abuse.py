"""Phase 10: hostile clients on the staff WebSocket (beyond tests/test_realtime.py).

No first message other than a valid staff access token ever subscribes a socket, nothing a
client sends can crash the server's side of the socket, and a diner's cookie is not a staff
credential.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.rate_limit import Limit, Limits
from tests.test_realtime import Closed, Connect, StaffSocket, closed_with, receive

CLOSED_FOR_BAD_INPUT = {4401, 1003, 1007, 1009}


async def send_bytes(socket: StaffSocket, data: bytes) -> None:
    await socket._to_app.put({"type": "websocket.receive", "bytes": data})


def minted(settings: Settings, crew: dict, **changes: object) -> str:
    """A token signed with the real key but with claims an attacker would like to change."""
    claims = jwt.decode(crew["chef_token"], options={"verify_signature": False})
    claims.update(changes)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


async def test_no_hostile_first_message_ever_subscribes(
    connect: Connect, crew: dict, tuned: Callable[..., None], app: object
) -> None:
    tuned(ws_auth_timeout_seconds=0.3)
    settings: Settings = app.state.settings  # type: ignore[attr-defined]
    claims = jwt.decode(crew["chef_token"], options={"verify_signature": False})
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    later = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    tokens = {
        "alg none": f"{header}.{payload}.",
        "another key": jwt.encode(claims, "k" * 48, algorithm="HS256"),
        "wrong type": minted(settings, crew, typ="refresh"),
        "wrong audience": minted(settings, crew, aud="someone-else"),
        "no session id": minted(settings, crew, sid=None),
        "expired": minted(settings, crew, exp=1_000_000_000),
        "issued in the future": minted(settings, crew, nbf=later, iat=later),
        "unknown user": minted(settings, crew, sub="00000000-0000-0000-0000-000000000000"),
        "another restaurant": minted(settings, crew, rid="00000000-0000-0000-0000-000000000000"),
        "oversized": "a" * 7000,
    }
    first_messages: list[object] = [
        None,
        [],
        "auth",
        {"type": "auth", "access_token": None},
        {"type": "auth", "access_token": [crew["chef_token"]]},
        {"type": "auth", "access_token": {"$ne": ""}},
        {"type": ["auth"], "access_token": crew["chef_token"]},
        json.loads('{"a":' * 1000 + "1" + "}" * 1000),
        *({"type": "auth", "access_token": token} for token in tokens.values()),
    ]
    for message in first_messages:
        async with connect() as socket:
            await socket.send_json(message)
            assert await closed_with(socket) in CLOSED_FOR_BAD_INPUT, message
    async with connect() as socket:
        await send_bytes(socket, json.dumps({"type": "auth", "access_token": "x"}).encode())
        assert await closed_with(socket) in CLOSED_FOR_BAD_INPUT


async def test_garbage_after_authentication_never_breaks_the_socket(
    connect: Connect, crew: dict
) -> None:
    async with connect(crew["chef_token"]) as socket:
        for junk in (
            "not json",
            "null",
            "[]",
            '{"type": 42}',
            '{"type": "subscribe", "restaurant_id": "00000000-0000-0000-0000-000000000000"}',
        ):
            await socket.send_text(junk)
        await socket.send_json({"type": "ping"})
        assert (await receive(socket))["type"] == "pong"  # still alive, still only its own
    async with connect(crew["chef_token"]) as socket:
        await send_bytes(socket, b"\x00\xff" * 100)
        try:
            await socket.send_json({"type": "ping"})
            assert (await receive(socket))["type"] == "pong"
        except Closed as closed:  # closing on a binary frame is fine too — cleanly
            assert closed.code in CLOSED_FOR_BAD_INPUT


async def test_a_diners_cookie_is_not_a_staff_credential(
    connect: Connect, crew: dict, tuned: Callable[..., None]
) -> None:
    tuned(ws_auth_timeout_seconds=0.3)
    cookie = {"cookie": "__Host-ts=" + "a" * 43}
    async with connect(headers=cookie) as socket:
        assert await closed_with(socket) == 4401


async def test_one_address_cannot_hold_endless_sockets(
    connect: Connect, crew: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Limits, "WS_CONNECT_PER_IP", Limit("ws_connect_ip", 3, 300))
    for _ in range(3):  # e.g. a tablet reconnecting after Wi-Fi drops
        async with connect(crew["chef_token"]):
            pass
    async with connect() as socket:
        assert await closed_with(socket) == 4429
