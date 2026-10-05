"""Phase 10 sweep: every route, attacked according to the declared surface (surface.py).

* the declaration matches the application exactly (no undeclared or under-protected route);
* no staff or customer route answers without valid credentials;
* every role reaches exactly what its permissions allow, and a forced password change blocks
  everything else;
* another restaurant's ids — in the path or in the body — are never found and change nothing;
  a diner cannot touch another table's orders;
* every anonymous route is rate limited.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, get_args

import asyncpg
import httpx
import jwt
import pytest
from fastapi.routing import APIWebSocketRoute
from pydantic import BaseModel

from app.core.permissions import ROLE_PERMISSIONS
from app.models.enums import Role
from app.services.table_session_service import SESSION_COOKIE
from tests.conftest import (
    ALL_TABLES,
    INVITE_CODE,
    PASSWORD,
    SeededRestaurant,
    Seeder,
    create_table,
    current_pin,
    login_ok,
    open_table_session,
    staff_headers,
)
from tests.security.surface import (
    PNG,
    SURFACE,
    WEBSOCKETS,
    Access,
    Kind,
    body_for,
    declared_access,
    fill,
    http_routes,
    walk_routes,
)
from tests.test_menu import build_menu

ClientFactory = Callable[[str], httpx.AsyncClient]
ROLES = ("owner", "admin", "waiter", "kitchen")


def routes_of(*kinds: Kind) -> list[tuple[str, Access]]:
    return [(key, access) for key, access in SURFACE.items() if access.kind in kinds]


async def send(
    client: httpx.AsyncClient,
    key: str,
    *,
    headers: dict[str, str] | None = None,
    ids: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    method, path = key.split(" ", 1)
    headers = dict(headers or {})
    if key in ("POST /api/v1/orders", "POST /api/v1/public/orders"):
        headers["Idempotency-Key"] = f"sweep-{uuid.uuid4().hex[:20]}"
    url = fill(path, ids or {})
    if key == "POST /api/v1/menu/items/{item_id}/image":
        return await client.post(
            url, headers=headers, files={"image": ("dish.png", PNG, "image/png")}
        )
    if method in ("POST", "PUT", "PATCH"):
        return await client.request(method, url, headers=headers, json=body or {})
    return await client.request(method, url, headers=headers)


@dataclass
class World:
    """One restaurant with every kind of thing in it, and someone of every role."""

    restaurant: SeededRestaurant
    staff: dict[str, dict[str, str]]  # role -> bearer headers
    ids: dict[str, str]
    customer: httpx.AsyncClient  # holds a live table-session cookie


async def build_world(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder, code: str, ip: str
) -> World:
    restaurant = await seed.restaurant(code)
    await seed.user(restaurant, "admin", "boss")
    await seed.user(restaurant, "waiter", "ravi")
    await seed.user(restaurant, "kitchen", "chef")
    victim = await seed.user(restaurant, "waiter", "victim")
    staff = {
        "owner": await staff_headers(client, code, "owner"),
        "admin": await staff_headers(client, code, "boss"),
        "waiter": await staff_headers(client, code, "ravi"),
        "kitchen": await staff_headers(client, code, "chef"),
    }
    built = await build_menu(client, staff["owner"])
    table = await create_table(client, staff["owner"], "T1")
    order = await client.post(
        "/api/v1/orders",
        headers={**staff["waiter"], "Idempotency-Key": f"world-{code}-0001"},
        json={
            "table_id": table["id"],
            "items": [{"menu_item_id": built["naan"]["id"], "quantity": 1}],
        },
    )
    assert order.status_code == 201, order.text
    customer = client_from(ip)
    pin = await current_pin(client, staff["owner"])
    assert (await open_table_session(customer, table["qr_token"], pin)).status_code == 200
    diner_order = await customer.post(
        "/api/v1/public/orders",
        headers={"Idempotency-Key": f"world-{code}-0002"},
        json={"items": [{"menu_item_id": built["naan"]["id"], "quantity": 2}]},
    )
    assert diner_order.status_code == 201, diner_order.text
    return World(
        restaurant=restaurant,
        staff=staff,
        ids={
            "user_id": str(victim),
            "table_id": table["id"],
            "qr_token": table["qr_token"],
            "category_id": built["mains"]["id"],
            "item_id": built["naan"]["id"],
            "group_id": built["spice"]["id"],
            "modifier_id": built["options"][0]["id"],
            "order_id": order.json()["id"],
            "diner_order_id": diner_order.json()["id"],
        },
        customer=customer,
    )


async def fingerprint(conn: asyncpg.Connection, restaurant_id: uuid.UUID) -> dict[str, str]:
    """A digest of every row a restaurant owns, to prove an attack changed nothing."""
    digests = {
        "restaurants": await conn.fetchval(
            "SELECT md5(r::text) FROM restaurants r WHERE id = $1", restaurant_id
        )
    }
    for table in ALL_TABLES[1:]:
        # Table names come from the ALL_TABLES constant.
        digests[table] = await conn.fetchval(
            f"SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), '')) "  # noqa: S608
            f"FROM {table} t WHERE restaurant_id = $1",
            restaurant_id,
        )
    return digests


# --- the declaration ------------------------------------------------------------------------------


def test_every_route_is_declared_with_the_protection_it_really_has(app: Any) -> None:
    actual = http_routes(app)
    assert set(actual) == set(SURFACE), (
        f"undeclared: {sorted(set(actual) - set(SURFACE))}; "
        f"gone: {sorted(set(SURFACE) - set(actual))}"
    )
    for key, route in actual.items():
        declared = SURFACE[key]
        found = declared_access(route)
        if declared.kind in (Kind.STAFF, Kind.SIGNED_IN, Kind.CUSTOMER):
            assert found == Access(declared.kind, declared.permissions), key
        else:
            assert found is None, key
        if declared.kind is Kind.ANONYMOUS:
            assert declared.limit is not None, f"{key} must name its rate limit"
    sockets = {path for path, route in walk_routes(app) if isinstance(route, APIWebSocketRoute)}
    assert sockets == WEBSOCKETS


# --- credentials ----------------------------------------------------------------------------------


def _forged(token: str) -> dict[str, str]:
    claims = jwt.decode(token, options={"verify_signature": False})
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode())
    unsigned = f"{header.decode().rstrip('=')}.{payload.decode().rstrip('=')}."
    return {
        "signed with another key": jwt.encode(claims, "x" * 48, algorithm="HS256"),
        "alg none": unsigned,
        "expired": jwt.encode({**claims, "exp": 1_000_000_000}, "x" * 48, algorithm="HS256"),
    }


async def test_staff_routes_refuse_requests_without_valid_credentials(
    client: httpx.AsyncClient, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    tokens = await login_ok(client, "alpha", "owner")
    cases = {
        "no header": {},
        "garbage": {"Authorization": "Bearer not-a-token"},
        "refresh token as bearer": {"Authorization": f"Bearer {tokens['refresh_token']}"},
        "basic auth": {"Authorization": "Basic b3duZXI6Q29ycmVjdC1Ib3JzZS00Mg=="},
        "token in a cookie": {"Cookie": f"access_token={tokens['access_token']}"},
        **{
            label: {"Authorization": f"Bearer {token}"}
            for label, token in _forged(tokens["access_token"]).items()
        },
    }
    for key, _ in routes_of(Kind.STAFF, Kind.SIGNED_IN):
        for label, headers in cases.items():
            response = await send(client, key, headers=headers)
            assert response.status_code == 401, (key, label, response.status_code)
            assert "access_token" not in response.text


async def test_customer_routes_refuse_requests_without_a_live_table_session(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    ended = world.customer.cookies[SESSION_COOKIE]
    assert (await world.customer.delete("/api/v1/public/session")).status_code in (200, 204)
    cases = {
        "no cookie": None,
        "garbage cookie": "x" * 43,
        "ended session": ended,
        "oversized cookie": "a" * 5000,
    }
    for label, cookie in cases.items():
        diner = client_from(f"198.51.100.{10 + len(label)}")
        if cookie is not None:
            diner.cookies.set(SESSION_COOKIE, cookie)
        for key, _ in routes_of(Kind.CUSTOMER):
            response = await send(diner, key, ids=world.ids, body=body_for(key, world.ids))
            assert response.status_code == 401, (key, label, response.status_code)
    # A staff token is not a table session.
    for key, _ in routes_of(Kind.CUSTOMER):
        response = await send(
            client_from("198.51.100.99"),
            key,
            headers=world.staff["owner"],
            ids=world.ids,
            body=body_for(key, world.ids),
        )
        assert response.status_code == 401, key


# --- roles ----------------------------------------------------------------------------------------


async def test_each_role_reaches_exactly_what_its_permissions_allow(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    outcomes: dict[str, int] = {"allowed": 0, "refused": 0}
    for role in ROLES:
        allowed = ROLE_PERMISSIONS[Role(role)]
        for key, access in routes_of(Kind.STAFF):
            # Random ids in the path: allowed requests end in 404/409/422, never with a change
            # to the world's real data.
            response = await send(
                client, key, headers=world.staff[role], body=body_for(key, world.ids)
            )
            if access.permissions <= allowed:
                assert response.status_code not in (401, 403), (role, key, response.text)
                outcomes["allowed"] += 1
            else:
                assert response.status_code == 403, (role, key, response.status_code)
                outcomes["refused"] += 1
    # Every staff route x 4 roles; waiters and kitchen staff are refused most management routes.
    assert outcomes["allowed"] + outcomes["refused"] == 4 * len(routes_of(Kind.STAFF))
    assert outcomes["refused"] > 60, outcomes


async def test_a_forced_password_change_blocks_everything_else(
    client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    await seed.user(world.restaurant, "admin", "fresh", must_change_password=True)
    fresh = await staff_headers(client, "alpha", "fresh")
    for key, _ in routes_of(Kind.STAFF):
        response = await send(client, key, headers=fresh, body=body_for(key, world.ids))
        assert response.status_code == 403, (key, response.status_code)
    assert (await client.get("/api/v1/auth/me", headers=fresh)).status_code == 200


# --- other restaurants' and other tables' data ----------------------------------------------------


async def test_another_restaurants_ids_are_never_found_and_nothing_changes(
    client: httpx.AsyncClient,
    client_from: ClientFactory,
    seed: Seeder,
    owner_conn: asyncpg.Connection,
) -> None:
    alpha = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    bravo = await build_world(client, client_from, seed, "bravo", "198.51.100.2")
    before = await fingerprint(owner_conn, alpha.restaurant.id)

    attacked = 0
    for key, _ in routes_of(Kind.STAFF):
        if "{" not in key or key.endswith("{decision}") and "order_id" not in key:
            continue
        for role in ("owner", "admin"):  # every permission there is
            response = await send(
                client,
                key,
                headers=bravo.staff[role],
                ids=alpha.ids,
                body=body_for(key, bravo.ids),
            )
            assert response.status_code == 404, (role, key, response.status_code)
            attacked += 1
    alpha_diner_order = {**alpha.ids, "order_id": alpha.ids["diner_order_id"]}
    for key, _ in routes_of(Kind.CUSTOMER):
        if "{order_id}" in key:
            response = await send(bravo.customer, key, ids=alpha_diner_order, body={})
            assert response.status_code == 404, key

    id_routes = [key for key, _ in routes_of(Kind.STAFF) if "{" in key]
    assert attacked == 2 * len(id_routes) == 54  # every route taking an id, as owner and admin
    assert await fingerprint(owner_conn, alpha.restaurant.id) == before


async def test_ids_from_another_restaurant_inside_a_body_are_refused(
    client: httpx.AsyncClient,
    client_from: ClientFactory,
    seed: Seeder,
    owner_conn: asyncpg.Connection,
) -> None:
    alpha = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    bravo = await build_world(client, client_from, seed, "bravo", "198.51.100.2")
    before = await fingerprint(owner_conn, alpha.restaurant.id)
    orders_before = await owner_conn.fetchval(
        "SELECT count(*) FROM orders WHERE restaurant_id = $1", bravo.restaurant.id
    )
    owner = bravo.staff["owner"]
    mine, theirs = bravo.ids, alpha.ids
    attempts = [
        (
            "POST /api/v1/orders",
            owner,
            {},
            {
                "table_id": theirs["table_id"],
                "items": [{"menu_item_id": mine["item_id"], "quantity": 1}],
            },
        ),
        (
            "POST /api/v1/orders",
            owner,
            {},
            {
                "table_id": mine["table_id"],
                "items": [{"menu_item_id": theirs["item_id"], "quantity": 1}],
            },
        ),
        (
            "POST /api/v1/orders",
            owner,
            {},
            {
                "table_id": mine["table_id"],
                "items": [
                    {
                        "menu_item_id": mine["item_id"],
                        "quantity": 1,
                        "modifier_ids": [theirs["modifier_id"]],
                    }
                ],
            },
        ),
        (
            "POST /api/v1/menu/items",
            owner,
            {},
            {"category_id": theirs["category_id"], "name": "Smuggled"},
        ),
        ("PATCH /api/v1/menu/items/{item_id}", owner, mine, {"category_id": theirs["category_id"]}),
        (
            "PUT /api/v1/menu/items/{item_id}/modifier-groups",
            owner,
            mine,
            {"group_ids": [theirs["group_id"]]},
        ),
        (
            "POST /api/v1/problem-reports",
            owner,
            {},
            {"category": "order_mistake", "order_id": theirs["order_id"]},
        ),
    ]
    for key, headers, ids, body in attempts:
        response = await send(client, key, headers=headers, ids=ids, body=body)
        assert response.status_code in (404, 422), (key, body, response.status_code)
    diner = await send(
        bravo.customer,
        "POST /api/v1/public/orders",
        body={"items": [{"menu_item_id": theirs["item_id"], "quantity": 1}]},
    )
    assert diner.status_code in (404, 422), diner.text

    assert await fingerprint(owner_conn, alpha.restaurant.id) == before
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM orders WHERE restaurant_id = $1", bravo.restaurant.id
        )
        == orders_before
    )


async def test_a_diner_cannot_touch_another_tables_orders(
    client: httpx.AsyncClient,
    client_from: ClientFactory,
    seed: Seeder,
    owner_conn: asyncpg.Connection,
) -> None:
    world = await build_world(client, client_from, seed, "alpha", "198.51.100.1")
    other_table = await create_table(client, world.staff["owner"], "T2")
    pin = await current_pin(client, world.staff["owner"])
    neighbour = client_from("198.51.100.3")
    assert (await open_table_session(neighbour, other_table["qr_token"], pin)).status_code == 200

    cancel = await neighbour.post(
        f"/api/v1/public/orders/{world.ids['diner_order_id']}/cancel", json={}
    )
    assert cancel.status_code == 404
    assert (await neighbour.get("/api/v1/public/orders")).json() == []
    status = await owner_conn.fetchval(
        "SELECT status FROM orders WHERE id = $1", uuid.UUID(world.ids["diner_order_id"])
    )
    assert status == "placed"


# --- rate limits ----------------------------------------------------------------------------------


@pytest.mark.parametrize("key", [key for key, _ in routes_of(Kind.ANONYMOUS)])
async def test_every_anonymous_route_is_rate_limited(
    key: str, client: httpx.AsyncClient, client_from: ClientFactory, seed: Seeder
) -> None:
    await seed.restaurant("alpha")
    owner = await staff_headers(client, "alpha", "owner")
    table = await create_table(client, owner, "T1")
    limit = SURFACE[key].limit
    assert limit is not None
    bodies = {
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
    attacker = client_from("203.0.113.77")
    statuses = []
    for _ in range(limit.max + 1):
        response = await send(
            attacker, key, ids={"qr_token": table["qr_token"]}, body=bodies.get(key)
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            break
    assert statuses[-1] == 429, (key, statuses[-3:])
    assert response.headers.get("retry-after"), key


def test_every_request_body_refuses_fields_it_does_not_define(app: Any) -> None:
    """No mass assignment: a body can never smuggle in restaurant_id, status, price, role…"""

    def models(annotation: Any) -> Iterator[type[BaseModel]]:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
            for field in annotation.model_fields.values():
                yield from models(field.annotation)
        for arg in get_args(annotation):
            yield from models(arg)

    checked = set()
    for key, route in http_routes(app).items():
        for param in route.dependant.body_params:
            for model in models(param.field_info.annotation):
                assert model.model_config.get("extra") == "forbid", (key, model.__name__)
                checked.add(model)
    assert len(checked) >= 25, len(checked)
