"""Phase 10 sweep: hostile input in every field, query parameter, path segment and body shape.

Whatever arrives, the API answers with a clean 4xx (or stores the text literally and returns it
as JSON data); it never fails with a 5xx, never reflects the input back in an error, never leaks
database or Python internals, and never runs anything the input says.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import asyncpg
import httpx

from tests.conftest import INVITE_CODE, PASSWORD, Seeder
from tests.security.surface import SURFACE, Kind, body_for, fill, http_routes
from tests.security.test_surface import World, build_world, send

ClientFactory = Callable[[str], httpx.AsyncClient]

PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE orders; --",
    '" OR 1=1 --',
    "1); SELECT pg_sleep(3); --",
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(document.cookie)>',
    "javascript:alert(1)",
    "{{7*7}}${7*7}<%= 7*7 %>",
    "\x00null byte",
    "line one\r\nSet-Cookie: injected=1",
    "\u202eevil\u202c reversed",
    "zero\u200bwidth",
    "%00%27%22%3Cscript%3E",
    "A" * 10_000,
]
LEAKS = ("Traceback", "asyncpg", "sqlalchemy", "psycopg", "syntax error at", "pg_catalog")

# Bodies with every optional text field filled in, so each one gets attacked.
EXTRA_TEXT: dict[str, dict[str, Any]] = {
    "POST /api/v1/orders": {"notes": "x"},
    "POST /api/v1/public/orders": {"notes": "x"},
    "POST /api/v1/orders/{order_id}/cancel": {"reason": "x"},
    "POST /api/v1/orders/{order_id}/cancellation-request/{decision}": {"note": "x"},
    "POST /api/v1/public/orders/{order_id}/cancel": {"reason": "x"},
    "POST /api/v1/menu/categories": {"description": "x"},
    "PATCH /api/v1/restaurant": {"name": "x", "timezone": "Asia/Kolkata"},
    "POST /api/v1/staff": {"email": "new@example.com"},
}
ANONYMOUS_BODIES: dict[str, dict[str, Any]] = {
    "POST /api/v1/auth/login": {
        "restaurant_code": "alpha",
        "username": "owner",
        "password": "Wrong-Pass-123",
    },
    "POST /api/v1/auth/refresh": {"refresh_token": "a" * 43},
    "POST /api/v1/auth/password-reset/request": {
        "restaurant_code": "alpha",
        "email": "nobody@alpha.example",
    },
    "POST /api/v1/auth/password-reset/confirm": {
        "token": "a" * 43,
        "new_password": "New-Pass-2026-xyz",
    },
    "POST /api/v1/restaurants": {
        "restaurant_name": "Spam",
        "restaurant_code": "spam-house",
        "owner_display_name": "Spam",
        "owner_username": "spam",
        "password": PASSWORD,
        "invite_code": INVITE_CODE + "-wrong",
    },
    "POST /api/v1/public/tables/{qr_token}/session": {"pin": "0000"},
}


def base_body(key: str, ids: dict[str, str]) -> dict[str, Any] | None:
    body = ANONYMOUS_BODIES.get(key) or body_for(key, ids)
    if body is None:
        return None
    body = {**body, **EXTRA_TEXT.get(key, {})}
    for line in body.get("items", []):
        line["notes"] = "x"
    return body


def string_leaves(value: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(value, str):
        yield path
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from string_leaves(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from string_leaves(child, (*path, index))


def replaced(value: Any, path: tuple[Any, ...], new: Any) -> Any:
    if not path:
        return new
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        return {**value, head: replaced(value[head], rest, new)}
    return [replaced(v, rest, new) if i == head else v for i, v in enumerate(value)]


def assert_clean(response: httpx.Response, key: str, payload: str) -> None:
    assert response.status_code < 500, (key, payload[:40], response.status_code, response.text)
    for leak in LEAKS:
        assert leak not in response.text, (key, leak)
    if response.status_code >= 400:
        body = response.json()
        assert set(body) == {"error", "request_id"}, (key, body)
        if len(payload) > 3:  # never reflected back in an error
            assert payload not in response.text, (key, payload[:40])


def caller_for(world: World, key: str, client: httpx.AsyncClient) -> tuple[Any, dict[str, str]]:
    access = SURFACE[key]
    if access.kind is Kind.CUSTOMER:
        return world.customer, {}
    if access.kind in (Kind.STAFF, Kind.SIGNED_IN):
        return client, world.staff["owner"]
    return client, {}


def reset_limits(app: Any) -> None:
    app.state.rate_limiter.clear()  # attack the validation, not the rate limiter


async def test_hostile_text_in_every_field_is_rejected_or_stored_literally(
    app: Any,
    client: httpx.AsyncClient,
    client_from: ClientFactory,
    seed: Seeder,
    owner_conn: asyncpg.Connection,
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    tables_before = await owner_conn.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    )
    requests = 0
    for key in SURFACE:
        if key.startswith(("GET ", "DELETE ")) or key.endswith(("/logout", "/logout-all")):
            continue
        body = base_body(key, world.ids)
        if body is None:
            continue
        caller, headers = caller_for(world, key, client)
        for leaf in string_leaves(body):
            for payload in PAYLOADS:
                reset_limits(app)
                started = time.monotonic()
                response = await send(
                    caller, key, headers=headers, ids=world.ids, body=replaced(body, leaf, payload)
                )
                assert time.monotonic() - started < 2.5, (key, payload)  # pg_sleep never ran
                assert_clean(response, key, payload)
                requests += 1
    assert requests > 500, requests

    # Still all there, and anything accepted was stored as plain text.
    assert (
        await owner_conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        == tables_before
    )
    assert await owner_conn.fetchval("SELECT count(*) FROM orders") >= 2
    stored = await owner_conn.fetch("SELECT name FROM menu_categories")
    assert any(row["name"] == "'; DROP TABLE orders; --" for row in stored)


async def test_hostile_query_strings_and_path_segments_never_break_anything(
    app: Any, client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    segments = [
        "' OR 1=1--",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "..\\..\\windows\\win.ini",
        "%00",
        "00000000-0000-0000-0000-000000000000",
        "-1",
        "A" * 2000,
        "<script>",
    ]
    routes = http_routes(app)
    for key, access in SURFACE.items():
        method, path = key.split(" ", 1)
        caller, headers = caller_for(world, key, client)
        # Query parameters the route declares.
        for param in routes[key].dependant.query_params:
            for payload in PAYLOADS:
                reset_limits(app)
                response = await caller.request(
                    method,
                    fill(path, world.ids),
                    headers=headers,
                    params={param.alias: payload},
                    **({"json": base_body(key, world.ids) or {}} if method != "GET" else {}),
                )
                assert_clean(response, key, payload)
        # Every path parameter, one at a time.
        for param in routes[key].dependant.path_params:
            if access.kind is Kind.INFRA and param.name != "key":
                continue
            for segment in segments:
                reset_limits(app)
                target = path.replace("{" + param.name + "}", segment).replace(
                    "{" + param.name + ":path}", segment
                )
                response = await caller.request(
                    method,
                    fill(target, world.ids),
                    headers=headers,
                    **({"json": base_body(key, world.ids) or {}} if method != "GET" else {}),
                )
                assert response.status_code in (400, 401, 403, 404, 405, 409, 422), (
                    key,
                    segment[:30],
                    response.status_code,
                )
                assert_clean(response, key, segment)


async def test_malformed_bodies_get_a_clean_error_on_every_route(
    app: Any, client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    malformed: dict[str, tuple[bytes, str]] = {
        "deeply nested arrays": (b"[" * 100_000, "application/json"),
        "deeply nested objects": (b'{"a":' * 30_000 + b"1" + b"}" * 30_000, "application/json"),
        "NaN and Infinity": (
            b'{"expected_version": NaN, "quantity": Infinity}',
            "application/json",
        ),
        "huge integer": (b'{"expected_version": ' + b"9" * 5000 + b"}", "application/json"),
        "invalid UTF-8": (b'{"name": "\xff\xfe\xfd"}', "application/json"),
        "truncated": (b'{"name": "Soup', "application/json"),
        "top-level array": (b"[1, 2, 3]", "application/json"),
        "top-level string": (b'"hello"', "application/json"),
        "null": (b"null", "application/json"),
        "duplicate keys": (b'{"name": "a", "name": "b"}', "application/json"),
        "ten thousand keys": (
            ("{" + ",".join(f'"k{i}": {i}' for i in range(10_000)) + "}").encode(),
            "application/json",
        ),
        "form encoded": (b"name=Soup&restaurant_id=1", "application/x-www-form-urlencoded"),
        "plain text": (b'{"name": "Soup"}', "text/plain"),
        "xml": (
            b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>"
            b"<x>&e;</x>",
            "application/xml",
        ),
        "over the size limit": (b'{"name": "' + b"a" * 1_100_000 + b'"}', "application/json"),
    }
    routes = http_routes(app)
    checked = 0
    for key in SURFACE:
        method, path = key.split(" ", 1)
        if method not in ("POST", "PUT", "PATCH") or key.endswith("/image"):
            continue
        takes_body = routes[key].body_field is not None
        caller, headers = caller_for(world, key, client)
        for label, (content, content_type) in malformed.items():
            reset_limits(app)
            response = await caller.request(
                method,
                fill(path, world.ids),
                content=content,
                headers={
                    **headers,
                    "Content-Type": content_type,
                    "Idempotency-Key": f"bad-{uuid.uuid4().hex[:16]}",
                },
            )
            if takes_body:
                assert 400 <= response.status_code < 500, (key, label, response.status_code)
            assert_clean(response, key, "")  # routes without a body ignore it, cleanly
            checked += 1
    assert checked > 400, checked


async def test_input_the_database_refuses_is_a_400_not_a_500() -> None:
    """The safety net behind validation: SQLSTATE class 22 (data exceptions) means bad input."""
    from sqlalchemy.exc import DBAPIError
    from starlette.requests import Request

    from app.core.errors import _database_error

    class Orig(Exception):
        def __init__(self, sqlstate: str) -> None:
            self.sqlstate = sqlstate

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {}})
    nul = DBAPIError("SELECT 1", {}, Orig("22021"))  # invalid byte sequence (a NUL character)
    deadlock = DBAPIError("SELECT 1", {}, Orig("40P01"))
    assert (await _database_error(request, nul)).status_code == 400
    assert (await _database_error(request, deadlock)).status_code == 500
